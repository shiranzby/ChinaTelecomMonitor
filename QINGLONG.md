# 青龙面板适配说明

## 背景

青龙面板（Qinglong）是一个定时任务管理平台，支持 Python 脚本定时执行。  
电信脚本（`telecom_query.py`）基于纯 API 登录，**无需 Playwright 浏览器**，比移动脚本更容易在青龙环境中运行。

---

## 快速适配步骤

### 1. 安装 Python 依赖

在青龙「依赖管理」→「Python 依赖」中添加：

```
requests
pycryptodome
```

> `pycryptodome` 用于 AES 解密电信 API 响应，是必需依赖。

### 2. 上传脚本文件

将以下文件上传到青龙「脚本管理」：

- `telecom_query.py`
- `telecom_config.example.json`（参考模板）

### 3. 创建配置文件

在脚本同级目录创建 `telecom_config.json`，参考 `telecom_config.example.json` 填写：

```json
{
  "输出设置": {
    "城市": 1,
    "余额": 1,
    "本月消费": 1,
    "通用流量": 1,
    "专用流量": 1,
    "总流量": 1,
    "查询时间": 1
  },
  "手机号": [
    {
      "号码": "138xxxx1234",
      "服务密码": "123456"
    }
  ]
}
```

> ⚠️ 服务密码为电信 **6 位数字服务密码**，非登录密码。忘记可在电信 APP 或拨打 10000 重置。

### 4. 测试运行

在青龙「脚本管理」中找到 `telecom_query.py`，点击「运行」测试。  
首次运行会登录并缓存 Token，后续运行直接使用缓存 Token，无需重复登录。

### 5. 创建定时任务

在青龙「定时任务」中新建任务：

| 字段 | 值 |
|------|-----|
| 名称 | 电信话费查询 |
| 命令 | `task telecom_query.py` |
| 定时规则 | `30 7 * * *`（每日早 7:30 执行，按需调整） |

---

## 通知推送（青龙环境）

### 方式一：使用脚本内置推送（推荐）

在 `telecom_config.json` 中配置 `通知推送` 段，脚本会自动调用 SMTP / PushPlus / Server 酱等渠道推送结果。

```json
{
  "通知推送": {
    "启用": true,
    "推送渠道": {
      "SMTP服务器": "smtp.qq.com",
      "SMTP端口": 465,
      "SMTP_SSL": true,
      "发件邮箱": "your@qq.com",
      "邮箱密码或授权码": "your_auth_code",
      "收件邮箱": "receiver@example.com"
    }
  }
}
```

### 方式二：接入青龙内置通知

修改 `telecom_query.py` 的 `send_notify()` 函数，在推送逻辑末尾增加青龙通知调用：

```python
def send_notify(title, body, notify_config):
    # ... 原有推送逻辑 ...

    # 青龙通知（调用 ql 命令）
    import subprocess, json as _json
    try:
        # 读取青龙环境变量中的通知配置
        result = subprocess.run(
            ["ql", "notify", title, body],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass
```

如不修改代码，也可在定时任务的「任务输出」中查看查询结果，或依赖脚本内置的 SMTP/PushPlus 等推送渠道。

---

## 登录风控保护

脚本内置登录风控保护机制：

- 连续登录失败 **5 次** 自动停止，防止账号被锁定
- 失败计数保存在 `data/fail_count.json`，重置需手动删除该文件
- Token 缓存在 `data/tokens.json`，有效期约 **7~30 天**

如 Token 过期，删除 `data/tokens.json` 后重新运行脚本即可自动重新登录。

---

## Docker + 青龙

如果青龙环境 Python 依赖安装困难，可改用 Docker 容器运行，然后在定时任务中调用：

```bash
# 青龙定时任务命令改为：
docker run --rm \
  -v /ql/data/scripts/telecom_config.json:/app/telecom_config.json:ro \
  -v /ql/data/scripts/data:/app/data \
  telecom-monitor
```

需在青龙「配置文件」→「脚本」目录中放置 `telecom_config.json` 和 `data/` 目录。

---

## 常见问题

**Q：服务密码和登录密码有什么区别？**  
A：服务密码是电信 6 位数字密码，用于 API 登录；登录密码是电信 APP/官网的密码。两个不同，需分别保管。

**Q：Token 缓存有效期多久？**  
A：约 7~30 天，视电信风控策略而定。过期后脚本会自动重新登录。

**Q：多号码怎么配置？**  
A：在 `telecom_config.json` 的 `手机号` 列表中添加多个号码即可，查询时会自动并发执行。

**Q：青龙里 `pycryptodome` 安装失败？**  
A：需在青龙宿主机上执行 `pip install pycryptodome`，或改用 Docker 方式运行（参见上文「Docker + 青龙」）。
