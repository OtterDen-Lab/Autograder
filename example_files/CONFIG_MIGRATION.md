# Configuration Migration Guide

## Overview

The autograder configuration system has been refactored to support a cleaner, more hierarchical structure. Both **legacy** and **new** formats are supported for backward compatibility.

## What Changed

### Original Problem
- Settings were scattered across multiple levels (`kwargs`, top-level params, `assignment_kwargs`)
- No clear hierarchy or consistent pass-through mechanism
- Same course needed multiple entries for different assignment types (CST334-LLs, CST334-PAs)
- No formal kind→grader mappings

### New Solution
1. **TypeRegistry**: Define assignment types once with default graders and settings
2. **Assignment Groups**: Group related assignments within a single course
3. **Hierarchical Settings**: Settings flow cleanly: type defaults → course → group → assignment
4. **Simplified Assignment Format**: Support both simple IDs (`506883`) and dict format (`{id: 506883, repo_path: PA1}`)

## New Format Structure

```yaml
# Define assignment types with default graders and settings
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      record_retention: true

  text:
    kind: TextAssignment
    grader: TextSubmissionGrader
    settings:
      grade_after_lock_date: true
      prefer_anthropic: true

courses:
  - name: CST334
    id: 29978
    slack_channel: C09HR2J5EBF

    assignment_groups:
      - name: Programming Assignments
        type: programming
        settings:
          base_image_name: "samogden/cst334"
          source_repo: "https://github.com/..."
        assignments:
          - 506889  # Simple ID format
          - {id: 506890, repo_path: PA2}  # Dict format with additional fields

      - name: Learning Logs
        type: text
        settings:
          prefer_anthropic: false  # Override type default
        assignments:
          - 506883  # LL5
          - 506884  # LL6
```

## Legacy Format (Still Supported)

```yaml
courses:
  - name: CST334
    id: 29978
    grader: TextSubmissionGrader
    slack_channel: "C09HR2J5EBF"
    assignment_defaults:
      kind: TextAssignment
      prefer_anthropic: true
      kwargs:
        grade_after_lock_date: true  # Must be in kwargs!
    assignments:
      - id: 506883
      - id: 506884
```

## Migration Steps

### 1. Fix Immediate Bug (Legacy Format)

If using legacy format and `grade_after_lock_date` isn't working:

**Before (BROKEN):**
```yaml
assignment_defaults:
  kind: TextAssignment
  grade_after_lock_date: true  # Wrong - not passed through!
```

**After (FIXED):**
```yaml
assignment_defaults:
  kind: TextAssignment
  kwargs:
    grade_after_lock_date: true  # Correct - in kwargs!
```

### 2. Migrate to New Format (Recommended)

**Step 1: Define assignment types**
```yaml
assignment_types:
  text:
    kind: TextAssignment
    grader: TextSubmissionGrader
    settings:
      grade_after_lock_date: true
      prefer_anthropic: true
```

**Step 2: Consolidate courses**

Instead of separate `CST334-LLs` and `CST334-PAs` entries, use one entry with assignment_groups:

```yaml
courses:
  - name: CST334
    id: 29978
    slack_channel: C09HR2J5EBF

    assignment_groups:
      - name: Programming Assignments
        type: programming
        settings:
          # PA-specific settings
        assignments:
          - 506889
          - 506890

      - name: Learning Logs
        type: text
        settings:
          # LL-specific overrides
        assignments:
          - 506883
          - 506884
```

## Key Benefits

1. **Single source of truth**: Define types once, reuse everywhere
2. **Clear hierarchy**: type → course → group → assignment
3. **Simplified IDs**: Just use `506883` instead of `{id: 506883}` when that's all you need
4. **One course entry**: CST334 PAs and LLs in single course definition
5. **Consistent settings**: No more confusion about kwargs vs top-level

## Examples

See:
- `workhorse-new-format.yaml` - Complete example using new format
- `workhorse.yaml` - Fixed legacy format
- `learning-logs.yaml` - Fixed legacy format

## Technical Details

### Settings Hierarchy

Settings merge in this order (later overrides earlier):
1. Type defaults (`assignment_types.{type}.settings`)
2. Course-level settings (any key not in reserved list)
3. Group settings (`assignment_groups[].settings`)
4. Assignment settings (any key except `id` in dict format)

### Reserved Keys

- Course level: `name`, `id`, `assignment_groups`, `assignment_defaults`, `assignments`, `grader`
- Group level: `name`, `type`, `assignments`, `settings`
- Assignment level: `id`

All other keys are treated as settings and merged hierarchically.
