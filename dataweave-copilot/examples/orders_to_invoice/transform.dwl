%dw 2.0
import * from dw::core::Numbers
output application/json
---
do {
	var activeItems = payload.lineItems filter ($.status != "cancelled")
	var subtotal = activeItems reduce ((item, acc = 0) -> acc + (item.quantity * item.unitPrice))
	var tax = round(subtotal * 0.08 * 100) / 100
	var total = round((subtotal + tax) * 100) / 100
	---
	{
		orderId: payload.orderId,
		customerName: payload.customer.name,
		lineItems: activeItems map (item) -> {
			sku: item.sku,
			description: item.description,
			quantity: item.quantity,
			unitPrice: item.unitPrice,
			lineTotal: round((item.quantity * item.unitPrice) * 100) / 100
		},
		subtotal: round(subtotal * 100) / 100,
		tax: tax,
		total: total
	}
}