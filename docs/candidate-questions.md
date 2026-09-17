# Candidate Questions & Answers

## 1. What approach would you use to automatically identify the Shopify category, attributes, and attribute values? Explain your approach and why you selected it.

In this project, I use a deterministic-first approach. I build normalized context from the product name, description, source categories, bullets, materials, and other useful fields. I then use an in-memory taxonomy index to generate a small candidate set instead of comparing the product with every category.

The deterministic scorer ranks those candidates using source fields, product-name evidence, descriptions, structured fields, hierarchy, product-type signals, and specificity. A confidence score and score margin decide whether the result is classified or needs review. Alternatives are stored for uncertain results. An optional provider-independent AI fallback can review the bounded candidate list, but it is disabled by default and deterministic results remain the fallback.

The taxonomy importer stores category attributes and possible values in separate models. Attribute extraction is represented by `ProductAttribute`; the current classification foundation is primarily focused on category selection, with the provider abstraction available for later enrichment.

## 2. How would you handle a product that has a title but no description and no image?

The current `ProductTextBuilder` still uses the title and any available structured or source-category fields. The classifier does not require a description or image to run. A strong title such as “Dining Chair” can still produce useful candidates.

If the remaining evidence is weak or competing candidates have a small score margin, the result is marked `needs_review` and alternatives are stored. I prefer that outcome to making an overconfident classification from insufficient information.

## 3. How would you use product images to improve classification when an image is available?

In the current prototype, the importer stores image URLs as `ProductImage` records with their original positions. It does not download images or inspect their contents during classification.

In a production system, I would add a separate image-processing stage. It could validate URLs, download images safely, extract visual signals, and pass a compact image description or embedding to the classifier. Image failures would be recorded per product and would not prevent text-based classification from completing.

## 4. How would you design the application to process 10,000+ products efficiently?

The current design is batch-oriented. Products belong to an `ImportBatch`, taxonomy categories are indexed in memory, and candidate generation is bounded. This avoids an O(products x all categories) comparison. Each product is isolated so one bad row or classification does not stop the rest.

Redis and Celery are configured, but no application Celery tasks are currently registered. For a production system, I would divide a batch into smaller jobs, process them with workers, persist progress, and apply provider rate limits and backoff. Database queries would use pagination, `select_related`, and bulk operations where safe.

## 5. How would you store the Shopify taxonomy and its category hierarchy in the database?

`TaxonomyCategory` stores the Shopify taxonomy ID, name, full path, active flag, and a nullable self-referencing `parent`. This represents the category tree without duplicating taxonomy data in each product.

`TaxonomyAttribute` belongs to a category, and `TaxonomyAttributeValue` belongs to an attribute. The taxonomy importer reads the official Shopify v2026-08 English release, imports categories in two passes, and then links attributes and values. The import is idempotent.

## 6. How would you calculate or determine the confidence score for a classification?

The deterministic scorer combines evidence from separate product fields. Its configurable weights give the strongest influence to source subcategory and product-name similarity, followed by source category, description/bullets, structured fields, and hierarchy. It also applies exact phrase, specificity, product-type, environment, and conflict adjustments.

The confidence decision also considers the margin between the best and second-best candidates. A high score with a sufficient margin is classified. A low score or a small margin is marked `needs_review`. The stored value remains between 0 and 1; it is not a claim of guaranteed accuracy.

## 7. What would you do when the system cannot confidently identify a single category?

I would keep the selected category only when there is a usable candidate, mark the result `needs_review`, and store up to three ranked alternatives. The reasoning field explains which product fields supported the deterministic decision.

If the AI fallback is enabled, it receives only the already-generated candidates. Its response is validated against those candidates. Invalid responses, conflicts, API errors, and rate limits retain the deterministic result. A reviewer can then select and approve a different active taxonomy category through the UI.

## 8. How would you handle a broken or inaccessible product image without stopping the complete batch?

For the current prototype, this is not a blocking issue because product import stores image URLs only and makes no HTTP requests. Image accessibility therefore cannot stop the import or deterministic classification pipeline.

In production, image validation and analysis would run separately with timeouts and per-image error handling. A failed image attempt would be recorded, while the product would continue through text and structured-data classification.

## 9. How would you design the API and database structure for this application?

The database separates concerns. `ImportBatch` owns source `Product` records, `ProductImage` stores image URLs, the taxonomy models store Shopify data, and classification models store results independently. `ClassificationResult` is one-to-one with a product; alternatives, extracted attributes, and processing attempts are related records.

The current prototype exposes Django template views and a bounded JSON taxonomy search endpoint at `/taxonomy/categories/?q=...`. The product list, detail page, review queue, import page, and classification page are implemented as Django views. A production API could expose the same boundaries using Django REST Framework or equivalent, with authentication and pagination added.

## 10. If the application needs to process 10,000 products and each external AI/API request takes approximately 2 seconds, how would you optimize the processing time?

First, I would avoid sending products to AI when deterministic evidence is already strong. That is the main optimization in this project. Candidate generation is bounded, so any AI request contains only relevant categories rather than the complete taxonomy.

For production, I would use background workers and process independent products concurrently, while enforcing the provider's request-per-minute and concurrency limits. I would cache reusable taxonomy indexes and safe repeatable results, use bounded retries with backoff for rate limits, and persist progress so a worker failure does not restart the entire batch.

## 11. How would you design the system so that if processing fails after 6,000 products, it can resume from the remaining products instead of starting again?

The current command requires an explicit import batch ID and selects its product scope before processing. It creates a `ClassificationResult` per successful product and a `ProcessingAttempt` for each execution. Existing results are skipped by default; `--overwrite` is required to intentionally reprocess them.

This means a later run can target the same batch and process products without existing results. Historical attempts are preserved, and row-level failures do not roll back successful products. In production, I would add explicit job state and worker checkpoints, but the persisted result and batch structure already support safe re-runs.

## 12. What technologies/frameworks would you choose for this application, and why?

I used Python and Django because they provide a clear model layer, migrations, management commands, forms, templates, and a practical admin interface. MariaDB stores the product, taxonomy, and classification data reliably. The local setup uses MariaDB on the configured development port.

The UI uses Django templates, plain CSS, and lightweight JavaScript. `pandas` and `openpyxl` support Excel import. Redis and Celery are configured for future background processing. The AI layer uses a `ClassificationProvider` interface, with Gemini as an optional implementation selected through environment settings and disabled by default.

## 13. Provide a high-level architecture/design for the complete application.

The source workbook goes through the products importer and produces `ImportBatch`, `Product`, and `ProductImage` records. The official Shopify taxonomy goes through a separate importer and produces category, attribute, and value records.

Products and taxonomy are then passed to the classification services: context preparation, candidate indexing/generation, deterministic scoring, confidence evaluation, optional AI fallback, and alternative generation. Results are stored in `ClassificationResult`, `ClassificationAlternative`, `ProductAttribute`, and `ProcessingAttempt`. The dashboard presents those records and supports manual review and approval.

Redis/Celery is configured as the future background execution layer. The current prototype runs the management command synchronously and does not register application Celery tasks.

## 14. Provide a realistic development effort estimation in hours, including a task-wise breakdown for developing this as a production-ready application. Mention assumptions and major dependencies/risks.

The machine-test prototype already covers the core models, importers, deterministic foundation, optional Gemini provider, batch command, and evaluator UI. A realistic production-ready extension would be approximately 240-360 hours, assuming one experienced Python/Django developer and an existing cloud environment.

| Area | Estimate |
|---|---:|
| Requirements, taxonomy review, and evaluation dataset | 16-24 hours |
| Production data model hardening and migrations | 16-24 hours |
| Import validation, resumability, and operational error storage | 24-36 hours |
| Classification quality improvements and evaluation tooling | 40-60 hours |
| Image validation and image-based signals | 24-40 hours |
| Background workers, queues, rate limits, and retries | 32-48 hours |
| API, authentication, authorization, and audit history | 32-48 hours |
| UI hardening and reviewer workflows | 20-32 hours |
| Monitoring, logging, alerts, and admin operations | 20-32 hours |
| Deployment, security, load testing, and documentation | 24-36 hours |

The estimate assumes the Shopify taxonomy format remains compatible and that product data is available for testing. Major risks are taxonomy updates, AI provider costs and rate limits, image quality and accessibility, lack of labeled evaluation data, and the operational complexity of asynchronous processing. I would validate classification quality with a representative reviewed dataset before treating the system as production-ready.

## Prototype Verification

The verified real sample run used ImportBatch `#6`:

- 4,999 products selected
- 4,999 processed
- 4,999 successful
- 0 failed
- 1,244 classified
- 3,755 initially needing review
- Approximately 127 seconds
- Deterministic/rule classifications were used in the full run
- Gemini calls: 0
- 58 Django tests passed
- `django check` passed
- `makemigrations --check` passed

The result does not claim that Gemini classified the full dataset. The prototype also does not claim 100% accuracy or production readiness.
