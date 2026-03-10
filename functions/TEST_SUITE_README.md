# Chronomaps API Test Suite

## Overview

This document describes the comprehensive test suite and improvements made to the Chronomaps API, including fallback mechanisms for missing Firestore indexes and automatic index creation.

## Changes Implemented

### 1. Aggregate Endpoint

**Location:** `functions/chronomaps_api/__init__.py` (line 371)

A new endpoint for aggregating items by field values with optional filtering.

**Endpoint:** `GET /<workspace>/items/aggregate`

**Query Parameters:**
- `field` (required) - The field name in metadata to aggregate by (supports nested fields with dot notation)
- `filters` (optional) - Pipe-separated filters to apply before aggregation (same format as `/items` endpoint)

**Response:**
- Returns a JSON array of objects with `value` and `count` fields
- Results sorted by count (most common first)
- Includes `null` for items without the specified field

**Example Usage:**
```bash
# Count items by status
GET /workspace-id/items/aggregate?field=status

Response:
[
  {"value": "active", "count": 15},
  {"value": "inactive", "count": 8},
  {"value": null, "count": 2}
]

# Count items by nested field
GET /workspace-id/items/aggregate?field=user.role

Response:
[
  {"value": "viewer", "count": 25},
  {"value": "editor", "count": 12},
  {"value": "admin", "count": 3}
]

# Count with filters
GET /workspace-id/items/aggregate?field=priority&filters=status==active

Response:
[
  {"value": 1, "count": 20},
  {"value": 2, "count": 15}
]
```

**Features:**
- Supports nested field paths (e.g., `user.role`, `metadata.tags`)
- Handles missing fields (counted as `null`)
- Works with string, numeric, boolean, array, and object values
- Results sorted by count descending (most common first)
- Supports filtering before aggregation
- Uses `fetch_and_filter_items` helper to avoid code duplication
- Requires authentication (admin, collaborate, or view access)
- Uses in-memory fallback if Firestore indexes are missing

### 2. Shared Filtering Logic

**Location:** `functions/chronomaps_api/__init__.py` (line 141)

To avoid code duplication between `/items` and `/items/aggregate` endpoints, a shared helper function was created:

**Function:** `fetch_and_filter_items(workspace, filters=None, order_by=None)`

**Features:**
- Consolidates item fetching and filtering logic
- Used by both `/items` and `/items/aggregate` endpoints
- Tries Firestore queries first, falls back to in-memory processing
- Returns items along with fallback/index status
- Reduces code duplication and ensures consistent behavior

### 3. In-Memory Filtering and Sorting Fallback

**Location:** `functions/chronomaps_api/__init__.py`

When a Firestore index is missing, the API now automatically falls back to in-memory filtering and sorting instead of returning an error. This ensures that the API remains functional even when indexes haven't been created yet.

**Key Features:**
- Detects when Firestore returns an "index required" error
- Fetches all items from the collection without filters/ordering
- Applies filters and sorting in Python memory
- Returns results with pagination as usual
- Adds headers to indicate fallback mode was used:
  - `X-Fallback-Mode: true` - Indicates fallback was used
  - `X-Index-URL: <url>` - URL to create the missing index

**Functions Added:**
- `apply_filters_in_memory(items, filters_str)` - Applies Firestore-style filters in memory
  - Supports operators: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `array-contains`
  - Handles nested field paths (e.g., `metadata.field_name`)

- `sort_items_in_memory(items, order_by_str)` - Sorts items in memory
  - Supports ascending and descending order
  - Handles None values (pushed to end of results)

### 4. Automatic Firestore Index Creation

**Location:** `functions/chronomaps_api/__init__.py`

When an index is missing, the system now automatically attempts to create it via the Firestore Admin API. This happens asynchronously in a background thread so it doesn't block the current request.

**Key Features:**
- Parses query parameters to determine required index fields
- Constructs proper Firestore index definition
- Calls Firestore REST API to create the index
- Runs in a background thread (non-blocking)
- Logs success/failure of index creation

**Function Added:**
- `create_firestore_index(workspace, order_by_field, filters_str)` - Creates Firestore composite index
  - Authenticates using Firebase Admin SDK credentials
  - Builds index definition with filter and order_by fields
  - Makes POST request to Firestore Admin API

**Environment Variables Required:**
- `GCP_PROJECT` or `GOOGLE_CLOUD_PROJECT` - GCP project ID for index creation

### 5. Comprehensive Test Suite

**Location:** `functions/tests/`

A comprehensive test suite covering all API endpoints and functionality.

**Test Files:**
- `tests/test_chronomaps_api.py` - Core API tests
- `tests/test_screenshot_handler.py` - Screenshot handler and image API tests

**Test Classes (test_chronomaps_api.py):**

1. **TestHelperFunctions** (4 tests)
   - Key generation
   - Author ID calculation
   - Metadata sanitization

2. **TestFilteringAndSorting** (8 tests)
   - Equality filters
   - Comparison filters (`<`, `>`, `<=`, `>=`)
   - `in` operator
   - `array-contains` operator
   - Multiple filters combined
   - Ascending/descending sorting
   - Handling None values in sorting

3. **TestAuthentication** (5 tests)
   - Admin authentication
   - Collaborate authentication
   - View authentication
   - Public access
   - Invalid key rejection

4. **TestWorkspaceEndpoints** (5 tests)
   - Create workspace
   - Create workspace preserves metadata
   - Get workspace metadata
   - Update workspace
   - Update workspace settings only
   - Delete workspace

5. **TestItemEndpoints** (4 tests)
   - Create item
   - Get item
   - Update item
   - Delete item

6. **TestItemsListingEndpoint** (3 tests)
   - Basic items listing
   - Pagination
   - Fallback mode when index is missing

7. **TestIndexCreation** (1 test)
   - Automatic index creation functionality

8. **TestAggregateEndpoint** (6 tests)
   - Aggregate by simple field
   - Aggregate by nested field
   - Missing field parameter validation
   - Aggregate with numeric values
   - Authentication requirement
   - Aggregate with filters

**Test Classes (test_screenshot_handler.py):**

9. **TestAnalyzeImage** (4 tests)
   - Returns analysis record from GPT-4 Vision
   - Automatic mode
   - Automatic mode with existing metadata context
   - Handles empty GPT response

10. **TestUploadImage** (2 tests)
    - Uploads to Firebase Storage and returns URL
    - Handles different content types (PNG, JPEG)

11. **TestFetchItem** (3 tests)
    - Successful item fetch
    - Error on 403 (unauthorized)
    - Error on 404 (not found)

12. **TestGetWorkspaceModeration** (3 tests)
    - Returns moderation level
    - Defaults to 3 when not set
    - Error on API failure

13. **TestDownloadItemImage** (2 tests)
    - Downloads image from Storage
    - Returns None when no image found

14. **TestScreenshotHandler** (3 tests)
    - Full flow: create new item
    - Full flow: update existing item
    - Error if item not found

15. **TestReplaceItemImage** (4 tests)
    - Replaces image successfully
    - No OpenAI analysis performed
    - Error if item not found
    - Error if not authorized

16. **TestReanalyzeItem** (6 tests)
    - Re-analyzes successfully
    - Preserves existing screenshot_url
    - Automatic mode passes existing metadata
    - Error if no image stored
    - Error if item not found
    - Updates item with new analysis

**Total:** 64 tests, all passing

### 6. CI/CD Integration

**Location:** `.github/workflows/`

Tests are now integrated into the CI/CD pipeline to ensure code quality before deployment.

**New/Modified Files:**
- `.github/workflows/test.yml` - Standalone test workflow for PRs and pushes
- `.github/workflows/deploy.yml` - Modified to run tests before deployment

**CI Workflow:**
1. Tests run on every push to `main` or `develop`
2. Tests run on all pull requests
3. Deployment only proceeds if tests pass
4. Coverage reports uploaded to Codecov

### 7. Testing Configuration

**Files:**
- `functions/pytest.ini` - Pytest configuration
- `functions/tests/conftest.py` - Test fixtures and Firebase mocking setup
- `functions/requirements-test.txt` - Testing dependencies:
  - `pytest>=7.0.0`
  - `pytest-cov>=4.0.0`
  - `pytest-mock>=3.10.0`

## Running Tests

### Locally

```bash
cd functions
python -m pytest tests/ -v --cov=chronomaps_api
```

### With Coverage Report

```bash
cd functions
python -m pytest tests/ -v --cov=chronomaps_api --cov-report=html
open htmlcov/index.html  # View coverage report in browser
```

### Run Specific Test Class

```bash
cd functions
python -m pytest tests/test_chronomaps_api.py::TestFilteringAndSorting -v
```

### Run Specific Test

```bash
cd functions
python -m pytest tests/test_chronomaps_api.py::TestFilteringAndSorting::test_apply_filters_equality -v
```

## API Behavior Changes

### Before

When a query required a Firestore index that didn't exist:
```json
{
  "index-required": "https://console.firebase.google.com/..."
}
```
HTTP Status: 412 (Precondition Failed)

### After

When a query requires a missing index:
1. API automatically falls back to in-memory filtering/sorting
2. Returns the expected results
3. Asynchronously creates the missing index in the background
4. Adds response headers indicating fallback mode:
   - `X-Fallback-Mode: true`
   - `X-Index-URL: <url>`

HTTP Status: 200 (Success)

## Performance Considerations

### Firestore Queries (Indexed)
- **Performance:** Fast - O(log n + k) where k is result size
- **Best for:** Production use with pre-created indexes

### In-Memory Filtering/Sorting (Fallback)
- **Performance:** Slower - O(n) for full collection scan
- **Best for:** Development, testing, or rare queries
- **Limitation:** All items must fit in memory

The fallback ensures the API works immediately, while background index creation optimizes for future requests.

## Future Improvements

1. **Index Caching:** Cache which indexes exist to avoid repeated fallback attempts
2. **Batch Index Creation:** Create multiple indexes at once based on common query patterns
3. **Metrics:** Track fallback usage to identify frequently used queries without indexes
4. **Index Status Monitoring:** Query Firestore to check if background index creation completed

## Troubleshooting

### Tests fail with "Firebase app does not exist"
- Ensure `serviceAccountKey.json` exists in the project root
- Or set `GOOGLE_APPLICATION_CREDENTIALS` environment variable

### Index creation fails
- Check that `GCP_PROJECT` or `GOOGLE_CLOUD_PROJECT` is set
- Verify Firebase Admin SDK has permissions to create indexes
- Check logs for specific error messages

### Tests timeout
- Increase timeout in pytest.ini or command line: `pytest --timeout=300`
- Check if Firestore is accessible from your network
