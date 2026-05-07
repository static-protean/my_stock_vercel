# Diamond OpenAPI

This reference summarizes the internal Diamond OpenAPI flow from the Yuque doc:
[OpenAPI 接入 Aone 分批发布流程](https://aliyuque.antfin.com/softloadblance/wpnllg/ue54v9)

## Publish API

- Method: `POST`
- URL: `https://diamond-inner.alibaba-inc.com/diamond-ops/order/v2/publish`

Required request fields:

- `dataId`
- `group`
- `appName`
- `targetEnvs`
- `content`
- `empId`
- `systemName`

Optional request fields:

- `callbackUrl`
- `type`
- `desc`
- `extraParams`

Notes:

- The request body is JSON.
- `targetEnvs` should not exceed 10 items.
- Domestic online center should use `sh` instead of `center`.

Typical success response fields:

- `code`
- `message`
- `data`

`data` contains order metadata such as publish order id and URL.

## Order Existence API

- Method: `GET`
- URL: `https://diamond-inner.alibaba-inc.com/diamond-ops/order/v2/isExist`

Required query params:

- `dataId`
- `group`

Typical response:

- `code`
- `message`
- `data` as boolean

## Callback

If `callbackUrl` is provided, Diamond may callback:

- `dataId`
- `group`
- `isSuccess`

The callback is GET-only according to the doc.

