# 400 参数不兼容 ≠ 模型不可用

> 关于 AI 模型探活工具里最容易被写错的一条判定逻辑。
>
> 本文对应项目：[AI-Model-Availability-Checker](https://github.com/peter8889/AI-Model-Availability-Checker)

## 起因

手上有十几个中转站的 Key，模型清单动辄一两百个。想知道哪些真能用，写的第一个脚本是「发一条 `hi`，看 HTTP 状态码」：

```python
if status == 200:
    print("可用")
else:
    print("不可用")
```

跑完发现：**184 个模型里只有 60 多个「可用」**，剩下的全被判成失败。但其中不少模型在官方网页端明明能用。

于是我去看那些失败请求的响应体，发现大量这样的：

```json
{
  "error": {
    "message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
    "type": "invalid_request_error",
    "code": "unsupported_parameter"
  }
}
```

状态码 `400`。

## 问题的本质

`400` 的含义是「请求有问题」，不是「模型不存在」。

这两件事被混在一起，会产生一个荒谬的结果：**模型是活的，能正常响应，甚至能准确告诉你它不吃哪个参数——但你把它标成了「不可用」**。

从服务端的视角看，这个区分非常清楚：

| 状态码 | 服务端实际在说什么 | 模型是否存在 |
| --- | --- | --- |
| `200` | 正常响应了 | 存在 |
| `400` 参数错误 | 「你这个参数我不认，换个写法」 | **存在，而且活着** |
| `401` | 「你的 Key 不对」 | 未知（根本没走到模型） |
| `403` | 「你有 Key，但没这个模型的权限」 | 存在，但被拦住 |
| `404` | 「没这个模型」 | **不存在** |
| `402` | 「余额不足」 | 存在，但没钱 |
| `429` | 「你太快了，慢点」 | 存在，只是限流 |
| `5xx` | 「我这边出问题了」 | 未知（上游故障） |

`400` 和 `404` 的差别，是「活着但不合身」和「根本不存在」的差别。

## 修正后的判定

改成七类之后，同样的 184 个模型，结果是：

```
可用      138
可用(受限)  26      <- 这一批就是被旧脚本误判为失败的
无权限      3
不存在      8
余额不足    1
限流        2
其他        6
```

**那 26 个「受限」模型，才是这套判定真正的价值。**

其中比较典型的几个：

- `gpt-5.1` 系列不吃 `max_tokens`，只认 `max_completion_tokens`
- `deepseek-reasoner` 在思考模式下直接忽略 `temperature`（传了不报错，但也不生效）
- `glm-5.3` 传 `thinking.type: disabled` 会直接报错，因为它只认 `max`/`high`/`low`
- `o3` 系列不接受 `temperature`，改了就 400

这些都不是「不能用」，是「用之前得知道它的脾气」。

## 实现：三级降级

判定为「可用（受限）」之后，还要能真正测出它的能力。做法是三级降级：

```python
# 第一轮：用最优参数（stream + tools + 标准 max_tokens）
status, body = request(payload_optimal)
if status == 200:
    return OK

# 第二轮：遇到 400 就看错误信息，定向修正参数
if status == 400 and "max_tokens" in body:
    payload = payload_flat          # 换掉 max_tokens
    status, body = request(payload_flat)
    if status == 200:
        return DEGRADED             # 可用，但受限

# 第三轮：还不行就退到最小请求，只要能通就说明模型活着
if status == 400:
    status, body = request(payload_minimal)
    if status == 200:
        return DEGRADED
```

关键是**第二轮要读错误信息，而不是无脑重试**。各家服务的错误文案不统一，所以用正则匹配：

```python
RETRYABLE_400 = re.compile(
    r"max_tokens|max_completion_tokens|temperature|top_p|"
    r"unsupported parameter|not supported with this model|"
    r"不支持|参数.*?(错误|无效|不支持)",
    re.I,
)
```

## 反面：不要把这些判成失败

写这类工具最容易犯的四个错：

**1. 把 `429` 判成不可用**

限流是临时状态，重试一次往往就过了。直接判死会让你以为模型挂了。

```python
if status == 429:
    time.sleep(retry_after or 2)
    return request_again()      # 而不是 return BAD
```

**2. 把 `5xx` 判成不可用**

上游故障 ≠ 模型不存在。`500`/`502`/`503` 都该重试。

**3. 把 `401` 当成单个模型的问题**

`401` 是 Key 级别的问题。如果第一个模型就 401，后面 183 个请求全都是浪费——应该**立即全局中止**。

**4. 把「列表里有」当成「能用」**

`GET /models` 返回的清单不可靠。有些中转站会把所有模型的 ID 都列出来，实际一个都调不通。必须逐个发真实请求。

## 一个更隐蔽的坑：思考参数

测「思考能力」时，各家的参数形态完全不同：

| 厂商 | 参数形态 | 实际含义 |
| --- | --- | --- |
| OpenAI | `reasoning_effort: none/low/medium/high/xhigh` | 真正的枚举分档 |
| Qwen | `enable_thinking: bool` + `thinking_budget: int` | 开关 + 预算 |
| DeepSeek | `thinking.type` + `reasoning_effort: low/high/max` | 开关 + 三档 |
| GLM-4.6 | `thinking.type: enabled/disabled` | **只有开关，没有档位** |
| GLM-5.3 | `reasoning_effort: max/high/low` | 传 `disabled` 会报错 |
| Gemini 3.x | `thinking_level` | 思考常开，不可关闭 |
| MiMo v2.6 | effort 名目多，但**实际不分档** | 传什么都一样 |

如果无脑用 OpenAI 的 `reasoning_effort` 去测所有模型，结论一定是错的：GLM-4.6 会报错，Gemini 会忽略，MiMo 会假装接受但行为不变。

正确做法是先按厂商域名 + 模型名推断参数形态，再构造请求：

```python
def resolve_think_mode(base, model):
    if "qwen" in model:
        return {"mode": "budget", "params": ["enable_thinking", "thinking_budget"]}
    if model.startswith("glm-4"):
        return {"mode": "switch", "params": ["thinking.type"]}
    if model.startswith("glm-5.3"):
        return {"mode": "enum", "params": ["reasoning_effort"], "levels": ["low","high","max"]}
    if model.startswith("gemini-3"):
        return {"mode": "enum", "params": ["thinking_level"], "cannot_disable": True}
    # ...
```

然后 `think_payload()` 把统一的档位值翻译成真实的请求参数。

这一步做完，你会发现有些模型的「思考不支持」其实是**你的参数写错了**，不是模型不行。

## 结论

一句话总结：**HTTP 状态码告诉你的信息，比「通/不通」二元判定要丰富得多。**

`400` 是个被浪费的信号。它不只是错误，它还携带了「这个模型活着、并且它不吃哪个参数」这一层信息。读懂它，你的检测精度会从「有没有」进化到「能不能、能到什么程度」。

具体到数字上：同一批 184 个模型，二元判定报 60 个可用，七类判定报 138 个可用 + 26 个可用受限。**差的不是 78 个模型，是你对这批 API 的真实认知。**

---

项目地址：[AI-Model-Availability-Checker](https://github.com/peter8889/AI-Model-Availability-Checker)

如果这篇对你有用，欢迎试用或提 issue。