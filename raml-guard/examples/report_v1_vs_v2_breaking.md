# raml-guard diff report

- Old spec: `examples/orders_api_v1.raml`
- New spec: `examples/orders_api_v2_breaking.raml`
- **Breaking changes: 7**
- Non-breaking changes: 0
- Informational changes: 0

## Breaking (7)

- **GET /orders/{orderId}** -- Resource '/orders/{orderId}' was removed entirely, eliminating GET /orders/{orderId}. (`method-removed`)
- **GET /orders query param 'status'** -- Parameter 'status' changed from optional to required; existing callers that omit it will now be rejected. (`param-now-required`)
- **GET /orders query param 'status'** -- Enum value(s) removed from the accepted set: ['SHIPPED']. (`enum-value-removed`)
- **GET /orders -> 200 body[application/json][].status** -- Enum value(s) removed from the accepted set: ['SHIPPED']. (`enum-value-removed`)
- **GET /orders -> 200 body[application/json][].total** -- Type narrowed from 'number' to 'integer' -- not every old value is valid under the new type. (`type-narrowed`)
- **POST /orders -> 201 body[application/json].status** -- Enum value(s) removed from the accepted set: ['SHIPPED']. (`enum-value-removed`)
- **POST /orders -> 201 body[application/json].total** -- Type narrowed from 'number' to 'integer' -- not every old value is valid under the new type. (`type-narrowed`)

## Non-Breaking (0)

_None._

## Informational (0)

_None._

