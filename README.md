# Shopify Product Taxonomy Classifier

## Overview

This project is a Python/Django prototype for a product-classification machine test. It imports a supplier Excel product catalogue, maps products to the Shopify Product Taxonomy, assigns confidence scores and alternatives, and routes uncertain results through a manual-review workflow.

The design is intentionally a modular Django monolith. It is suitable for evaluating the end-to-end workflow and is not presented as production-ready software.

## Key features

- Excel import for product data and image URLs
- Normalization of blank, numeric, boolean-like, and malformed optional values
- Row-level error isolation and import-batch counters
- Product images stored by source position; images are not downloaded
- Shopify Product Taxonomy category, attribute, and value import
- Deterministic candidate generation and scoring
- Confidence thresholds and manual-review identification
- Ranked alternative categories
- Optional provider-independent AI fallback architecture
- Gemini provider with bounded candidate prompts and rate-limit-safe fallback
- Processing attempts per product
- Django UI for dashboard, products, review, import, classification, and manual approval

## Architecture

```text
Excel workbook ──> products importer ──> ImportBatch / Product / ProductImage

Shopify v2026-08 ──> taxonomy importer ──> Category / Attribute / Value

Product + taxonomy
        │
        ▼
classification services
        ├─ text preparation
        ├─ candidate index/generation
        ├─ deterministic scoring
        ├─ optional AI fallback
        └─ ClassificationResult / Alternatives / Attempts
        │
        ▼
dashboard UI and manual review
```

- `products`: source products, images, import batches, and Excel importing.
- `taxonomy`: Shopify taxonomy models and import service.
- `classification`: text preparation, candidate indexing, scoring, confidence evaluation, AI provider abstraction, and classification persistence.
- `dashboard`: evaluator-facing Django templates, filters, import/classification forms, taxonomy search, and manual review.
- MariaDB: application database configured through environment variables.
- Redis/Celery: configured in the project for future asynchronous work. No application Celery tasks are currently registered.

## Classification pipeline

```text
Product
  → normalized text/context
  → bounded taxonomy candidate generation
  → deterministic category scoring
  → confidence and score-margin evaluation
  → optional AI fallback for ambiguous results
  → selected category and alternatives
  → classified or manual review
```

The classifier does not compare every product with every taxonomy category. It uses an in-memory taxonomy index and bounded candidate pools. Deterministic scoring remains the first stage. AI is considered only when enabled and deterministic confidence or margin rules require it.

Current confidence behavior is evidence-based: high scores with a sufficient margin can be classified; low confidence or close competing scores are marked for review. Provider errors, malformed responses, conflicts, and rate limits retain the deterministic result.

## Shopify taxonomy

The project uses the official Shopify Product Taxonomy release `v2026-08`, English distribution files stored locally under:

```text
data/taxonomy/shopify-v2026-08-en/
```

The importer accepts the extracted taxonomy directory or supported JSON input:

```powershell
python manage.py import_taxonomy "data\taxonomy\shopify-v2026-08-en"
```

Categories preserve Shopify IDs/GIDs in `TaxonomyCategory.taxonomy_id`, names, full paths, and parent relationships. Category attributes are stored in `TaxonomyAttribute`; possible values are stored in `TaxonomyAttributeValue`. The importer is idempotent, uses bulk operations where appropriate, and isolates malformed records.

## Optional AI integration

AI is provider-independent through `ClassificationProvider`. The current concrete provider is Gemini, selected through configuration. It receives only the normalized product context and bounded candidate categories already generated locally; the full taxonomy is never sent.

AI is disabled by default. When enabled, invalid responses, API errors, timeouts, and HTTP 429/`RESOURCE_EXHAUSTED` responses are handled safely. Gemini rate-limit retries are bounded and use the provider retry delay when available. If AI cannot complete, the deterministic decision remains the final result.

Never commit API keys or database credentials.

## Setup on Windows

Prerequisites:

- Python 3.11+
- MariaDB 10.5+
- Redis 6+ if using the configured Celery infrastructure

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py check
python manage.py migrate
python manage.py runserver
```

Configure `.env` with local values for `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and `REDIS_URL`. The project loads `.env` from the repository root. Keep `.env` untracked; use `.env.example` as the configuration template.

The local database used during development was MariaDB database `shopify_taxonomy` on port `3307`. Adjust the port if your installation differs.

The health endpoint is available at:

```text
http://127.0.0.1:8000/health/
```

Optional Celery worker command:

```powershell
celery -A config worker --loglevel=info
```

## Excel product import

```powershell
python manage.py import_products "path\to\Product List.xlsx"
```

The importer validates expected source headers, including product information, pricing, shipping fields, `Product URL`, and `Image 1` through `Image 20`. Incoming headers are trimmed before mapping. Missing optional values remain blank or NULL as appropriate; values are not invented or truncated.

Each row is processed independently. Invalid rows, duplicate product numbers within the same batch, invalid numeric values, and other row-level errors are reported with their Excel row number while the remaining rows continue. `ImportBatch` records total, processed, successful, and failed counts. Non-empty image URLs become `ProductImage` records with positions 1–20. No HTTP image downloads occur.

## Commands and UI

Import products:

```powershell
python manage.py import_products "path\to\products.xlsx"
```

Import taxonomy:

```powershell
python manage.py import_taxonomy "data\taxonomy\shopify-v2026-08-en"
```

Explicitly classify a batch:

```powershell
python manage.py classify_products --batch-id 6
```

Useful controlled-run options:

```powershell
python manage.py classify_products --batch-id 6 --limit 10
python manage.py classify_products --batch-id 6 --overwrite
```

The command requires an explicit batch ID. Existing results are skipped by default; `--overwrite` intentionally reprocesses them. The selected product scope is fixed before processing, and each selected product receives at most one processing attempt per invocation.

The Django UI is available at `/` after `runserver`:

- `/`: dashboard with batch-scoped statistics
- `/products/`: searchable, paginated product list
- `/products/<id>/`: product detail and manual category approval
- `/review/`: manual-review queue
- `/import/`: Excel upload
- `/classification/`: explicit batch classification form
- `/taxonomy/categories/?q=Dining+Table`: bounded taxonomy search endpoint
- `/admin/`: Django admin

## Database model overview

- `ImportBatch` → many `Product` records
- `Product` → many `ProductImage` records
- `TaxonomyCategory` → self-referencing parent/children hierarchy
- `TaxonomyCategory` → many `TaxonomyAttribute` records
- `TaxonomyAttribute` → many `TaxonomyAttributeValue` records
- `Product` → one `ClassificationResult`
- `ClassificationResult` → ranked `ClassificationAlternative` records
- `Product` → extracted `ProductAttribute` records
- `Product` → historical `ProcessingAttempt` records

Source product data and taxonomy data are kept separate from classification output.

## Testing and verified sample run

Run the test suite and Django checks:

```powershell
python manage.py test
python manage.py check
python manage.py makemigrations --check
```

The current suite contains 58 passing tests. Tests use isolated fixtures and mocked providers; they do not call Gemini.

The real imported Excel batch `#6` was processed successfully:

- Selected: 4,999
- Processed: 4,999
- Successful: 4,999
- Failed: 0
- Classified: 1,244
- Initially needing review: 3,755
- Duration: approximately 127 seconds
- Deterministic/rule classifications: 4,999
- Gemini calls: 0

The full run did not claim that Gemini classified these products.

## Manual review example

A low-confidence Trash Can result was reviewed through the Django UI. A reviewer selected the appropriate Shopify category and approved it manually. Manual approval updates the classification result to method `manual`, status `approved`, and clears the review flag while preserving alternatives and leaving Product and Taxonomy records unchanged.

## Design and scalability decisions

- Deterministic-first classification reduces unnecessary provider calls and keeps behavior explainable.
- Candidate generation is bounded rather than products × all taxonomy categories.
- Confidence and score-margin thresholds route ambiguous results to review.
- Row-level transaction boundaries prevent one bad import row from rolling back the batch.
- Import and classification are explicitly batch-oriented.
- Processing attempts preserve operational history.
- The provider interface allows a different AI provider without rewriting deterministic classification.
- Redis/Celery configuration provides a path to asynchronous workers for larger workloads, although production task orchestration is not yet implemented.

## Project structure

```text
config/
  settings.py, urls.py, celery.py
products/
  models.py
  services/importer.py
  management/commands/import_products.py
taxonomy/
  models.py
  services/importer.py
  management/commands/import_taxonomy.py
classification/
  models.py
  services/              # index, generator, scorer, engine, providers
  management/commands/classify_products.py
dashboard/
  views.py, forms.py, urls.py, tests.py
templates/dashboard/
data/taxonomy/shopify-v2026-08-en/
manage.py
requirements.txt
```

## Security and environment

Secrets and local credentials belong in `.env`, which must not be committed. This includes `DJANGO_SECRET_KEY`, database passwords, `GEMINI_API_KEY`, and provider configuration. Use `.env.example` as the safe template. Error messages and README documentation must never contain credentials or API keys.

## Limitations and future improvements

This is a machine-test prototype. Potential next steps include:

- richer image-based classification and image validation
- more sophisticated semantic ranking and evaluation datasets
- persistent import-error history and operational monitoring
- asynchronous distributed workers for larger production workloads
- broader human-review analytics and audit history
- additional provider implementations behind the existing abstraction

The prototype does not claim production readiness or 100% classification accuracy.
