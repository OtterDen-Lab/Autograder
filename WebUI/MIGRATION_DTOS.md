# Migration to DTOs - Summary

## What Changed

We migrated from dict-based data structures to strongly-typed Pydantic DTOs for exam processing.

## Files Created

### 1. `web_api/dtos/` - New Directory
- `__init__.py` - Exports `ProblemDTO` and `SubmissionDTO`
- `exam.py` - DTO definitions for exam processing

### 2. `web_api/services/blank_detection.py` - Example Service
Shows how to do cross-submission processing with DTOs.

## Files Modified

### 1. `web_api/services/exam_processor.py`
**Changed:**
- `process_exams()` now returns `Tuple[List[SubmissionDTO], List[SubmissionDTO]]`
- `redact_and_extract_regions()` returns `Tuple[str, List[ProblemDTO]]`
- `_build_submission_dict()` renamed to `_build_submission()` and returns `SubmissionDTO`

**Before:**
```python
submission = {
    "document_id": 1,
    "problems": [{"problem_number": 1, "image_base64": "..."}],
    "approximate_name": "John Doe"
}
```

**After:**
```python
submission = SubmissionDTO(
    document_id=1,
    problems=[ProblemDTO(problem_number=1, image_base64="...")],
    approximate_name="John Doe"
)
```

### 2. `web_api/routes/uploads.py`
**Changed:**
- Accessing DTO attributes instead of dict keys
- Simplified region_coords handling (already a dict in DTO)
- Added blank detection fields to Problem creation

**Before:**
```python
for sub_data in all_submissions_data:
    document_id = sub_data["document_id"]
    for prob_data in sub_data["problems"]:
        problem_number = prob_data["problem_number"]
```

**After:**
```python
for sub_dto in all_submissions_data:
    document_id = sub_dto.document_id
    for prob_dto in sub_dto.problems:
        problem_number = prob_dto.problem_number
```

## How to Use DTOs

### Accessing Data
```python
# Instead of dict access
submission["document_id"]  # OLD
submission.document_id      # NEW

# Instead of dict.get()
submission.get("student_name")  # OLD
submission.student_name         # NEW (None if not set)

# Iteration still works the same
for problem in submission.problems:
    print(problem.problem_number)
```

### Modifying Data
```python
# DTOs are mutable - changes persist
problem.is_blank = True
problem.mark_blank(confidence=0.95, method="population", reasoning="...")

# Changes are reflected in parent
submission.problems[0].is_blank = True
assert submission.problems[0].is_blank == True  # ✅
```

### Validation
```python
# Pydantic validates automatically
problem = ProblemDTO(
    problem_number=1,
    image_base64="...",
    region_coords={...},
    blank_confidence=1.5  # ❌ ValidationError: must be <= 1.0
)
```

### Serialization
```python
# Convert to dict for JSON
submission_dict = submission.dict()
# or (Pydantic v2)
submission_dict = submission.model_dump()

# Convert to JSON string
import json
json_str = submission.json()
# or (Pydantic v2)
json_str = submission.model_dump_json()
```

## Cross-Submission Processing Example

```python
from web_api.dtos import SubmissionDTO, ProblemDTO

def process_all_problem_ones(submissions: List[SubmissionDTO]):
    """Process all instances of problem 1 together"""

    # Collect all problem 1 instances
    problem_ones = [
        sub.get_problem(1)
        for sub in submissions
        if sub.get_problem(1) is not None
    ]

    # Calculate some statistic
    threshold = calculate_threshold(problem_ones)

    # Apply to each (modifies in place!)
    for problem in problem_ones:
        if should_mark_blank(problem, threshold):
            problem.mark_blank(0.95, "population", f"Threshold: {threshold}")

    # Changes are reflected in original submissions!
    # No need to manually update anything
```

## Benefits

1. **Type Safety**: IDE autocomplete and type checking catch errors
2. **Validation**: Pydantic validates data automatically
3. **Documentation**: Field types and descriptions are self-documenting
4. **Refactoring**: Easier to find all uses of a field
5. **Mutation**: Can modify nested objects and changes persist
6. **Behavior**: Can add methods to DTOs for common operations

## Backward Compatibility

DTOs can be converted to dicts if needed:
```python
# For legacy code that expects dicts
legacy_dict = submission.dict()
legacy_dict = submission.model_dump()  # Pydantic v2
```

## Testing

All existing tests should continue to work with minimal changes:
- Change dict access to attribute access
- DTOs can be created directly in tests with named arguments
- Mock objects can be replaced with real DTO instances
