# raml-guard diff report

- Old spec: `examples/orders_api_v1.raml`
- New spec: `examples/orders_api_v2_safe.raml`
- **Breaking changes: 0**
- Non-breaking changes: 12
- Informational changes: 0

## Breaking (0)

_None._

## Non-Breaking (12)

- **GET /orders** -- Description text changed (documentation-only). (`description-changed`)
- **GET /orders query param 'status'** -- Enum value(s) added, widening the accepted set: ['CANCELLED']. (`enum-value-added`)
- **GET /orders query param 'sortBy'** -- New optional parameter 'sortBy' added. (`param-added`)
- **GET /orders -> 200 body[application/json][].status** -- Enum value(s) added, widening the accepted set: ['CANCELLED']. (`enum-value-added`)
- **GET /orders -> 200 body[application/json][].giftMessage** -- New field 'giftMessage' added to the response body. (`field-added`)
- **POST /orders body[application/json].giftMessage** -- New field 'giftMessage' added to the request body (optional). (`field-added`)
- **POST /orders -> 202** -- Response status code 202 was added. (`response-status-added`)
- **POST /orders -> 201 body[application/json].status** -- Enum value(s) added, widening the accepted set: ['CANCELLED']. (`enum-value-added`)
- **POST /orders -> 201 body[application/json].giftMessage** -- New field 'giftMessage' added to the response body. (`field-added`)
- **DELETE /orders/{orderId}** -- Method DELETE /orders/{orderId} was added. (`method-added`)
- **GET /orders/{orderId} -> 200 body[application/json].status** -- Enum value(s) added, widening the accepted set: ['CANCELLED']. (`enum-value-added`)
- **GET /orders/{orderId} -> 200 body[application/json].giftMessage** -- New field 'giftMessage' added to the response body. (`field-added`)

## Informational (0)

_None._

