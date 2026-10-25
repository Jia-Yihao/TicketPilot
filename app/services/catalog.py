from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.retrieval.search import tokenize

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "catalog" / "catalog.json"
)


@dataclass(frozen=True, slots=True)
class CatalogSection:
    id: str
    title: str
    preview: str
    body: str

    @property
    def chars(self) -> int:
        return len(self.body)


@dataclass(frozen=True, slots=True)
class CatalogProduct:
    product_id: str
    sku: str
    name: str
    category: str
    price_cents: int
    currency: str
    stock: int
    warehouse: str
    warranty_months: int
    status: str
    summary: str
    badges: tuple[str, ...]
    order_ids: tuple[str, ...]
    fields: dict[str, str | int | bool | None]
    sections: tuple[CatalogSection, ...]

    @property
    def total_chars(self) -> int:
        return sum(section.chars for section in self.sections)


class CatalogService:
    """Local read-only catalog standing in for a product database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CATALOG_PATH
        self.products = _load_products(self.path)
        self._by_id = {
            key: product
            for product in self.products
            for key in (product.product_id.lower(), product.sku.lower())
        }
        self._by_order = {
            order_id: product
            for product in self.products
            for order_id in product.order_ids
        }

    def list_products(self) -> list[CatalogProduct]:
        return list(self.products)

    def get(self, product_id: str) -> CatalogProduct | None:
        return self._by_id.get(product_id.lower())

    def for_order(self, order_id: str) -> CatalogProduct | None:
        return self._by_order.get(order_id.upper())

    def resolve(self, text: str) -> CatalogProduct | None:
        normalized = text.lower()
        for key, product in self._by_id.items():
            if key in normalized:
                return product
        matches = [
            product
            for product in self.products
            if product.name.lower() in normalized
        ]
        if matches:
            return max(matches, key=lambda product: len(product.name))
        return None

    def search(self, query: str, *, limit: int = 3) -> list[CatalogProduct]:
        terms = set(tokenize(query))
        if not terms:
            return []
        scored: list[tuple[float, CatalogProduct]] = []
        for product in self.products:
            name_terms = set(tokenize(product.name))
            category_terms = set(tokenize(product.category))
            summary_terms = set(tokenize(product.summary))
            section_terms = set(
                token
                for section in product.sections
                for token in tokenize(f"{section.title} {section.preview}")
            )
            field_terms = set(
                token
                for value in product.fields.values()
                if isinstance(value, str)
                for token in tokenize(value)
            )
            score = (
                3.0 * len(terms & name_terms)
                + 2.0 * len(terms & category_terms)
                + 2.0 * len(terms & section_terms)
                + 1.0 * len(terms & summary_terms)
                + 0.5 * len(terms & field_terms)
            )
            if score > 0:
                scored.append((score, product))
        scored.sort(key=lambda item: (-item[0], item[1].product_id))
        return [product for _, product in scored[:limit]]


def _load_products(path: Path) -> tuple[CatalogProduct, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    products: list[CatalogProduct] = []
    for item in payload["products"]:
        products.append(
            CatalogProduct(
                product_id=item["product_id"],
                sku=item["sku"],
                name=item["name"],
                category=item["category"],
                price_cents=int(item["price_cents"]),
                currency=item.get("currency", "CNY"),
                stock=int(item.get("stock", 0)),
                warehouse=item.get("warehouse", ""),
                warranty_months=int(item.get("warranty_months", 0)),
                status=item.get("status", "ACTIVE"),
                summary=item["summary"],
                badges=tuple(item.get("badges", [])),
                order_ids=tuple(item.get("order_ids", [])),
                fields=dict(item.get("fields", {})),
                sections=tuple(
                    CatalogSection(
                        id=section["id"],
                        title=section["title"],
                        preview=section["preview"],
                        body=section["body"],
                    )
                    for section in item.get("sections", [])
                ),
            )
        )
    return tuple(products)
