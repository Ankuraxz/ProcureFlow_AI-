"""Declarative A2UI protocol surface schemas for native React widget rendering."""

from __future__ import annotations

from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

SurfaceTypeLiteral = Literal[
    "VendorComparisonCard",
    "BudgetGaugeCard",
    "AP2CartMandateApprovalCard",
    "OrderReceiptCard",
]


class VendorComparisonItem(BaseModel):
    """Single SKU quote option inside a `VendorComparisonCard`."""

    model_config = ConfigDict(extra="ignore")

    sku_id: str
    name: str
    vendor_id: str
    vendor_name: str
    vendor_tier: str
    unit_price_usd: float
    quantity: int = Field(default=1, ge=1)
    discount_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    subtotal_usd: float
    lead_time_days: int = Field(default=1, ge=0)
    specs: str = ""
    recommended: bool = False


class VendorComparisonPayload(BaseModel):
    """Wire payload contract for `VendorComparisonCard`."""

    model_config = ConfigDict(extra="ignore")

    items: list[VendorComparisonItem] = Field(min_length=1)
    currency: str = "USD"
    notes: str = ""


class BudgetGaugePayload(BaseModel):
    """Wire payload contract for `BudgetGaugeCard`."""

    model_config = ConfigDict(extra="ignore")

    department_id: str
    department_name: str
    budget_total_usd: float
    current_spent_usd: float
    order_amount_usd: float
    projected_spent_usd: float
    current_utilization_pct: float
    projected_utilization_pct: float
    warning_threshold_pct: float = 80.0
    status: Literal["HEALTHY", "WARNING_OVER_80", "EXCEEDED"]


class CartMandateLineItemPayload(BaseModel):
    """Single line item inside `AP2CartMandateApprovalCard` or `OrderReceiptCard`."""

    model_config = ConfigDict(extra="ignore")

    sku_id: str
    name: str
    quantity: int = Field(ge=1)
    unit_price_usd: float
    discount_pct: float = 0.0
    line_total_usd: float


class AP2CartMandateApprovalPayload(BaseModel):
    """Wire payload contract for `AP2CartMandateApprovalCard`."""

    model_config = ConfigDict(extra="ignore")

    cart_mandate_id: str
    user_id: str
    department_id: str
    vendor_id: str
    vendor_name: str
    vendor_tier: str
    line_items: list[CartMandateLineItemPayload] = Field(min_length=1)
    total_usd: float
    currency: str = "USD"
    cart_sha256: str
    canonical_payload_to_sign: str
    escalation_reasons: list[str] = Field(default_factory=list)
    status: Literal["PENDING_SIGNATURE", "SIGNED_SETTLED", "REJECTED"] = (
        "PENDING_SIGNATURE"
    )
    expires_at: str


class OrderReceiptPayload(BaseModel):
    """Wire payload contract for `OrderReceiptCard`."""

    model_config = ConfigDict(extra="ignore")

    order_id: str
    status: Literal["SETTLED"] = "SETTLED"
    authorization_mode: Literal["AUTO_INTENT_MANDATE", "HITL_CART_MANDATE"]
    vendor_name: str
    department_name: str
    total_usd: float
    line_items: list[CartMandateLineItemPayload] = Field(min_length=1)
    intent_mandate_id: str | None = None
    cart_mandate_id: str | None = None
    payment_mandate_id: str
    cart_sha256: str
    signature_preview: str
    estimated_delivery_days: int = Field(default=3, ge=0)
    created_at: str


SURFACE_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "VendorComparisonCard": VendorComparisonPayload,
    "BudgetGaugeCard": BudgetGaugePayload,
    "AP2CartMandateApprovalCard": AP2CartMandateApprovalPayload,
    "OrderReceiptCard": OrderReceiptPayload,
}


class A2UISurface(BaseModel):
    """Validated declarative A2UI envelope streamed over SSE to React widgets."""

    model_config = ConfigDict(extra="forbid")

    surface_id: str = Field(default_factory=lambda: f"srf_{uuid.uuid4().hex[:10]}")
    surface_type: SurfaceTypeLiteral
    title: str
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _validate_typed_payload(self) -> A2UISurface:
        model_cls = SURFACE_PAYLOAD_MODELS[self.surface_type]
        validated = model_cls.model_validate(self.payload)
        self.payload = validated.model_dump()
        return self


def create_a2ui_surface(
    surface_type: SurfaceTypeLiteral,
    title: str,
    payload: dict[str, Any] | BaseModel,
    surface_id: str | None = None,
) -> A2UISurface:
    """Construct and validate an `A2UISurface` instance."""
    raw_payload = payload.model_dump() if isinstance(payload, BaseModel) else dict(payload)
    kwargs: dict[str, Any] = {
        "surface_type": surface_type,
        "title": title,
        "payload": raw_payload,
    }
    if surface_id:
        kwargs["surface_id"] = surface_id
    return A2UISurface.model_validate(kwargs)
