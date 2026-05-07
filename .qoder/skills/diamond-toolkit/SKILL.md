---
name: diamond-toolkit
version: 0.2.0
description: Diamond 配置管理工具。当用户提到 Diamond、配置查询、配置获取、配置发布、Diamond 发布、MSE 配置中心、dataId、groupId、配置对比、配置变更时使用。支持从预发环境获取配置内容（API 优先），以及通过 OpenAPI 校验、查单、发布配置。
---

# Diamond 配置管理工具

## Overview

统一的 Diamond 配置管理 skill，提供两大核心能力：

1. **配置获取**：从 MSE 配置中心（预发环境）获取配置内容，API 优先，Playwright 备选
2. **配置发布**：通过 Diamond OpenAPI 校验、查单、发布配置

## 使用场景

### 配置获取（get）

- 查询业务开关、数据库连接、限流降级等配置
- 查看配置当前值
- 对比不同环境的配置差异

### 配置发布（publish）

- 发布 Diamond 配置变更
- 检查是否有在途发布工单
- 构建和校验发布请求体
- 获取发布工单 URL

---

## 一、配置获取

### 获取策略（优先级顺序）

1. **API 直接获取**（最快，2-3秒）— 调用 Diamond API，无需页面渲染，支持超大配置（100MB+）
2. **控制台格式化视图提取**（最可靠，3-5秒）— 自动展开"显示更多"，通过剪贴板获取完整内容
3. **JavaScript 编辑器提取**（3-5秒）— Monaco / Ace Editor API
4. **DOM 元素提取**（备选，5-8秒）— textarea / pre / code 标签
5. **HTML 正则提取**（兜底，仅调试模式）

### 核心参数

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `data_id` | 是 | 配置数据标识 | `cross.return.static.text.area` |
| `group_id` | 是 | 配置分组标识 | `ovs.base.crossReturn` |
| `namespace_id` | 否 | 命名空间ID（默认为空，即 Diamond 默认命名空间） | `ovs-base` |

### 使用方式

```bash
# 基本获取
python3 {{SKILL_PATH}}/scripts/get_diamond_config_with_login.py <dataId> <groupId> [namespaceId]

# 指定命名空间
python3 {{SKILL_PATH}}/scripts/get_diamond_config_with_login.py cross.return.static.text.area ovs.base.crossReturn ovs-base

# 调试模式（保存截图和 HTML）
python3 {{SKILL_PATH}}/scripts/get_diamond_config_with_login.py cross.return.static.text.area ovs.base.crossReturn --debug

# 无头模式（需已有登录凭证）
python3 {{SKILL_PATH}}/scripts/get_diamond_config_with_login.py cross.return.static.text.area ovs.base.crossReturn --headless
```

### 前置依赖

```bash
pip install playwright cryptography
playwright install chromium
```

### 注意事项

- 固定访问预发环境（pre）
- 首次使用需手动登录，凭证自动保存到 `~/.auto-login-credentials/`
- 支持 25KB+ 大配置，API 方式支持 100MB+

---

## 二、配置发布

### 工作流程

1. **收集参数** — 准备 `dataId`、`group`、`appName`、`targetEnvs`、`empId`、`systemName`、`content`
2. **校验** — 本地校验请求体
3. **查单** — 检查是否有在途工单
4. **发布** — 提交发布工单

### 必填字段

| 字段 | 说明 |
|------|------|
| `dataId` | Diamond dataId |
| `group` | Diamond group |
| `appName` | 应用名 |
| `targetEnvs` | 目标环境列表（最多 10 个） |
| `content` | 配置内容（字符串） |
| `empId` | 工号 |
| `systemName` | 系统名 |

可选字段：`type`、`desc`、`callbackUrl`、`extraParams`

### 使用方式

```bash
# 构建发布请求体
python3 {{SKILL_PATH}}/scripts/diamond_publish.py build-payload \
  --data-id xxx --group yyy --app-name ads-ai \
  --target-envs pre --content-file /path/to/content.json \
  --emp-id 123456 --system-name ads-ai

# 校验请求体
python3 {{SKILL_PATH}}/scripts/diamond_publish.py validate --payload-file /path/to/payload.json

# 检查是否有在途工单
python3 {{SKILL_PATH}}/scripts/diamond_publish.py is-exist --data-id xxx --group yyy

# 发布
python3 {{SKILL_PATH}}/scripts/diamond_publish.py publish --payload-file /path/to/payload.json
```

### Payload 格式

```json
{
  "dataId": "ads-ai-campaign-action-config-v1",
  "group": "ads-ai",
  "appName": "ads-ai",
  "targetEnvs": ["pre"],
  "content": "{...json string...}",
  "empId": "123456",
  "systemName": "ads-ai",
  "type": "json",
  "desc": "publish campaign action config"
}
```

### 安全规则

- 发布前必须先校验请求体并确认内容
- 缺少必填字段会被拒绝
- `targetEnvs` 不得超过 10 个环境
- 国内线上环境 `center` 自动归一化为 `sh`
- 脚本默认绕过本地 HTTP_PROXY（避免 TLS 握手问题）

---

## API 端点

| 用途 | 方法 | URL |
|------|------|-----|
| 获取配置 | GET | `https://mse.alibaba-inc.com/pre/diamond/api/config/getConfig` |
| 发布配置 | POST | `https://diamond-inner.alibaba-inc.com/diamond-ops/order/v2/publish` |
| 查询工单 | GET | `https://diamond-inner.alibaba-inc.com/diamond-ops/order/v2/isExist` |

## 脚本清单

| 脚本 | 用途 |
|------|------|
| `scripts/get_diamond_config_with_login.py` | 获取配置内容（API + Playwright） |
| `scripts/auto_login.py` | 自动登录模块（CDP + 凭证缓存） |
| `scripts/diamond_publish.py` | 发布配置（build-payload / validate / is-exist / publish） |

## 参考文档

- `references/diamond-openapi.md` — Diamond OpenAPI 字段说明和响应格式

## 错误处理

### 获取相关

- **配置不存在**：检查 dataId 和 groupId
- **API 获取失败**：自动回退到页面提取
- **登录超时**：重新运行脚本，手动完成登录

### 发布相关

- **校验失败**：检查必填字段和字段类型
- **工单冲突**：已有在途工单，需先处理
- **HTTP 错误**：检查网络和 Diamond 服务状态
