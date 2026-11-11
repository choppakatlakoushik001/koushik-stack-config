"""Frozen scorer for synthetic, single-function tasks; no code execution adapter."""
import json


def score_case(case, implementation):
    """A case passes only when every assertion returns the exact expected value/type.

    The trusted evaluation runner supplies a callable. This module neither loads
    generated source nor invokes a model, driver, routing policy, or subprocess.
    """
    try:
        for check in case['checks']:
            actual = implementation(*check['args'])
            expected = check['expected']
            if (type(actual) is not type(expected) or
                    json.dumps(actual, allow_nan=False) != json.dumps(expected, allow_nan=False)):
                return False
        return bool(case['checks'])
    except Exception:
        return False
