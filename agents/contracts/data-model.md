# Organizer data model

This contract defines the derived data model. Organizer files remain immutable; every record
must link back to its source hash and location.

## Source document

Required fields:

- `document_id`: `doc1`–`doc58` or a stable prospectus hash ID
- `product_codes`: zero or more path-derived `KR...` codes
- `source_group`: `general` or `product`
- `source_path`, `source_sha256`, `media_type`
- `title`, `issuer_or_author`, `audience`
- `printed_date`, `effective_date`, `modified_date` as separate nullable fields
- `authority`: task instruction, official rule, internal operations, customer guide, or marketing
- `review_status`: raw, extracted, OCR-reviewed, domain-reviewed, or quarantined

Never replace a missing effective date with the file modification date.

## Evidence chunk

- `chunk_id`, `document_id`, `product_codes`
- `page`, `slide`, `sheet`, section heading, or row coordinates
- `text`, `text_sha256`, extraction method, extraction confidence
- pension tags: DB, DC, IRP, pension savings, old personal pension, tax, procedure, product
- temporal and audience gates

Chunk by a semantic unit such as one Q&A, procedure, legal rule, table, or prospectus section.
Avoid mixing multiple pages or unrelated rules merely to reach a token size.

## Product master

One record per unique prospectus plus a many-to-many alias table for product codes:

- product name, manager, document/effective date, inception date
- legal/product category and asset type
- risk grade and grade basis
- purpose, strategy, asset allocation limits, benchmark
- redemption/purchase rules and material risks

The 100 product-code paths contain 92 unique PDF hashes. Preserve these duplicate-code groups:

- `KR5113420013`, `KR5113420015`
- `KR5153450009`, `KR518102001M`
- `KR5127420034`, `KR5127420039`
- `KR5127450215`, `KR5127450217`
- `KR5120450015`, `KR5120450018`
- `KR5113470030`, `KR5113470031`
- `KR5123490013`, `KR5123490016`, `KR5123490017`

The PDF body does not contain the path product code. Inject every alias into every product chunk.
Do not infer which sales class a code represents unless an authoritative mapping is supplied.

## Product child tables

Keep these one-to-many, never flatten them into one product value.

### Sales class and fees

- raw class name and normalized class traits
- pension/retirement eligibility
- front/back/no-load sales fee
- management, sales, trustee, and administration fees
- total fee, total fee-and-expense, synthetic total expense
- table unit, basis date, page, and raw row

### Performance

- class/fund/benchmark identity
- one-, two-, three-, five-year and since-inception values
- cumulative versus annualized basis, gross/net/tax basis
- measurement end date, unit, page, and raw row

A dash is missing/not-applicable data, not zero.

### Balance

- fund/class and accounting period
- beginning, subscription, redemption, reinvestment, and ending units/amount
- unit, basis date, page, and raw row

The documents contain historical period-end balance, not a field literally named current
market balance. Do not substitute manager AUM, offering limit, or financial-statement assets.

## Known review gates

- `doc29.xlsx` contains answer/branch inconsistencies and must remain quarantined until QA.
- `doc26.docx` includes `퇴직소득세 26만 4천만원`; likely intended meaning requires human approval.
- `doc41.docx` includes `148만 5천만원`; likely intended meaning requires human approval.
- Old-product and temporal gates are mandatory for `doc1`, `doc25`, `doc39`, and dated UI/process guides.
- Some product balance tables require OCR, notably `KR5120420091`, `KR5120450015`, and
  `KR5120450018`.

Preserve raw text and add corrections only in a separate reviewed annotation layer.
