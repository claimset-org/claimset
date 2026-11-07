"""Decision rules of confirmatory plans (lab record spec v0.16 §5.7.1–5.7.3).

A rule is either text, judged by the writer, or an expression over named
results and numeric parameters, which the tool evaluates. A plan that tests several hypotheses may give each
its own pair of rules (`rules`, keyed by hypothesis ID), over the quantities the plan shares; `effective()`
gives the single pair that decides one hypothesis. Expressions are parsed with Python's
`ast` module and only a small, fixed set of node types is accepted, so a rule
can compare and combine numbers and nothing else.
"""

import ast
import math
import operator
import re

from .. import validate as _claimset

# The frameworks, their required parameters and the outcomes are the claimset's (claimset §3.3.3).
FRAMEWORKS = tuple(sorted(_claimset.ENUM["framework"]))
REQUIRED = _claimset.TEST_PARAMETERS
SIDES = ("two_sided", "greater", "less")
FIELDS = _claimset.QUANTITY_FIELDS                                 # where a quantity reads a result (claimset §3.3.3)
OUTCOMES = ("supported", "not_supported", "inconclusive")          # the order verdicts are reported in
assert set(OUTCOMES) == _claimset.ENUM["outcome"]
TEXT_RULE_OUTCOME = {"supported": "supported", "not_supported": "not_supported", "neither": "inconclusive"}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
MAX_RULE_LENGTH = 1000

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMPOPS = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
           ast.Eq: operator.eq, ast.NotEq: operator.ne}


class RuleError(ValueError):
    pass


def _is_number(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _check_node(node, names):
    """Raise RuleError unless every node is allowed and every name is in `names`."""
    if isinstance(node, ast.Expression):
        return _check_node(node.body, names)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for v in node.values:
            _check_node(v, names)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
        _check_node(node.operand, names)
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        _check_node(node.left, names)
        _check_node(node.right, names)
    elif isinstance(node, ast.Compare) and all(type(op) in _CMPOPS for op in node.ops):
        _check_node(node.left, names)
        for c in node.comparators:
            _check_node(c, names)
    elif isinstance(node, ast.Name):
        if node.id not in names:
            raise RuleError(f"'{node.id}' is not a quantity or numeric parameter of the plan")
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool) and not _is_number(node.value):
            raise RuleError("a constant is too large")
        if not _is_number(node.value):
            raise RuleError(f"only numbers are allowed as constants, not {node.value!r}")
    elif isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "abs" and len(node.args) == 1
                and not node.keywords):
            raise RuleError("the only function allowed is abs(x)")
        _check_node(node.args[0], names)
    else:
        raise RuleError(f"'{type(node).__name__}' is not allowed in a rule")


def parse(expr, names):
    """Parse and check a rule expression; return its tree. Raise RuleError if it is not allowed."""
    if not isinstance(expr, str) or not expr.strip():
        raise RuleError("the rule is empty")
    if len(expr) > MAX_RULE_LENGTH:
        raise RuleError(f"the rule is longer than {MAX_RULE_LENGTH} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise RuleError(f"not a valid expression ({exc.msg})") from None
    _check_node(tree, set(names))
    return tree


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.BoolOp):
        vals = [bool(_eval(v, env)) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return (not v) if isinstance(node.op, ast.Not) else (-v if isinstance(node.op, ast.USub) else +v)
    if isinstance(node, ast.BinOp):
        right = _eval(node.right, env)
        if isinstance(node.op, ast.Div) and right == 0:
            raise RuleError("division by zero")
        return _BINOPS[type(node.op)](_eval(node.left, env), right)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Call):
        return abs(_eval(node.args[0], env))
    raise RuleError(f"'{type(node).__name__}' is not allowed in a rule")


RULE_KEYS = ("framework", "parameters", "supported", "not_supported")


def per_hypothesis(decision):
    """Whether the rule gives each hypothesis its own pair of rules (`rules`, v0.16)."""
    return isinstance(decision, dict) and "rules" in decision


def rule_key(decision, hypothesis, same=None):
    """The key of `rules` that decides `hypothesis`: the key itself, or a key that `same(key, hypothesis)` says is
    the same hypothesis (another version of it). None if there is none, or if the rule has no `rules`."""
    rules = decision.get("rules") if isinstance(decision, dict) else None
    if not isinstance(rules, dict):
        return None
    if hypothesis in rules:
        return hypothesis
    for k in rules:
        if same is not None and same(k, hypothesis):
            return k
    return None


def effective(decision, hypothesis=None, same=None):
    """The single pair of rules that decides `hypothesis`: the plan's shared framework, parameters and quantities,
    with the hypothesis's own `framework`, `parameters` (added to and overriding the shared ones) and rules. A
    rule without `rules` decides every hypothesis it names and is returned as it is."""
    if not per_hypothesis(decision):
        return decision
    out = {k: v for k, v in decision.items() if k != "rules"}
    key = rule_key(decision, hypothesis, same)
    own = decision["rules"].get(key) if key is not None else None
    own = own if isinstance(own, dict) else {}
    if own.get("framework") is not None:
        out["framework"] = own["framework"]
    if isinstance(own.get("parameters"), dict):
        shared = out.get("parameters") if isinstance(out.get("parameters"), dict) else {}
        out["parameters"] = dict(shared, **own["parameters"])
    for k in ("supported", "not_supported"):
        if own.get(k) is not None:
            out[k] = own[k]
    return out


def names_used(decision):
    """The names the two rules of a single-pair rule use (quantities and parameters); empty for text rules or
    rules that do not parse."""
    out = set()
    for k in ("supported", "not_supported"):
        try:
            tree = ast.parse(str(decision.get(k) or "").strip(), mode="eval")
        except SyntaxError:
            continue
        out |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    return out


def computed(decision):
    """Whether the rule's verdict is computed from results (it has quantities) rather than judged."""
    return isinstance(decision, dict) and isinstance(decision.get("quantities"), dict) \
        and bool(decision["quantities"])


def numeric_parameters(decision):
    params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
    return {k: v for k, v in params.items() if isinstance(k, str) and NAME_RE.match(k) and _is_number(v)}


def problems(decision, runs=1, hypotheses=None, same=None):
    """Everything that makes a decision rule incomplete or invalid (rule L15); empty if it is complete.

    `runs` is the plan's number of deciding runs: with more than one, each quantity names its run. `hypotheses`
    are the IDs the plan names: with `rules`, there is one entry for each of them and for nothing else.
    """
    if not isinstance(decision, dict):
        return ["decision must be an object {framework, parameters, quantities, supported, not_supported}"]
    if per_hypothesis(decision):
        return _problems_per_hypothesis(decision, runs, hypotheses, same)
    out = []
    fw = decision.get("framework")
    if fw not in FRAMEWORKS:
        return [f"framework must be one of {', '.join(FRAMEWORKS)}, not {fw!r}"]
    params = decision.get("parameters", {})
    if not isinstance(params, dict):
        out.append("parameters must be an object")
        params = {}
    for key in REQUIRED.get(fw, ()):
        v = params.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(f"the {fw} framework needs the parameter '{key}'")
    if "alpha" in params and not (_is_number(params["alpha"]) and 0 < params["alpha"] < 1):
        out.append("alpha must be a number between 0 and 1")
    if "sides" in params and params["sides"] not in SIDES:
        out.append(f"sides must be one of {', '.join(SIDES)}")
    for key in ("lower_bound", "upper_bound", "threshold"):
        if key in params and not _is_number(params[key]):
            out.append(f"{key} must be a number")
    if _is_number(params.get("lower_bound")) and _is_number(params.get("upper_bound")) \
            and not params["lower_bound"] < params["upper_bound"]:
        out.append("lower_bound must be below upper_bound")

    names = set(numeric_parameters(decision))
    q = decision.get("quantities")
    if q is not None and not isinstance(q, dict):
        out.append("quantities must be an object {name: {variable, conditions, field, run}}")
    elif q:
        for name, spec in q.items():
            if not NAME_RE.match(str(name)):
                out.append(f"quantity name '{name}' must start with a letter and use only letters, digits and _")
            elif name in names:
                out.append(f"'{name}' is both a quantity and a parameter")
            if isinstance(spec, dict) and "count" in spec:
                # a count over the codings, as records begun before lab record v0.20 wrote it (qualitative §2.4)
                from ..extensions.qualitative_count import spec_problems
                if "quantity" in spec or "variable" in spec:
                    out.append(f"quantity '{name}' is a count or binds a result's variable, not both")
                bad = sorted(set(spec) - {"count", "run"})
                if bad:
                    out.append(f"quantity '{name}' is a count: it has only count and run, not {', '.join(bad)}")
                out += spec_problems(name, spec["count"])
            elif not isinstance(spec, dict) or not isinstance(bound_variable(spec), str) or not bound_variable(spec).strip():
                out.append(f"quantity '{name}' needs the variable of the result it binds")
                continue
            if spec.get("conditions") is not None and not isinstance(spec["conditions"], dict):
                out.append(f"conditions of quantity '{name}' must be an object")
            if spec.get("field", "value") not in FIELDS:
                out.append(f"field of quantity '{name}' must be one of {', '.join(FIELDS)}")
            r = spec.get("run")
            if r is not None and not (isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= max(runs, 1)):
                out.append(f"run of quantity '{name}' must be a deciding run's number, 1 to {runs}")
            elif r is None and isinstance(runs, int) and runs > 1:
                out.append(f"quantity '{name}' must say which deciding run it comes from (\"run\": 1 to {runs})")
        names |= set(q)
        for outcome in ("supported", "not_supported"):
            try:
                parse(decision.get(outcome), names)
            except RuleError as exc:
                out.append(f"the '{outcome}' rule: {exc}")
    else:
        for outcome in ("supported", "not_supported"):
            v = decision.get(outcome)
            if not isinstance(v, str) or not v.strip():
                out.append(f"the '{outcome}' rule must say what observation decides it")
    return out


def _problems_per_hypothesis(decision, runs, hypotheses, same):
    rules = decision.get("rules")
    if not isinstance(rules, dict) or not rules:
        return ["rules must be an object with one entry per hypothesis: {\"H1\": {\"supported\": …, "
                "\"not_supported\": …}, …}"]
    out = []
    for k in ("supported", "not_supported"):
        if decision.get(k) is not None:
            out.append(f"a rule with per-hypothesis 'rules' has no shared '{k}' rule; give it in each entry")
    if hypotheses is not None:
        named = [h for h in hypotheses if isinstance(h, str)]
        missing = [h for h in named if rule_key(decision, h, same) is None]
        extra = [k for k in rules if not any(k == h or (same is not None and same(k, h)) for h in named)]
        if missing:
            out.append(f"rules has no entry for {', '.join(missing)}, which the plan tests")
        if extra:
            out.append(f"rules has an entry for {', '.join(map(str, extra))}, which the plan does not test")
    for k, own in rules.items():
        if not isinstance(own, dict):
            out.append(f"the rules for {k} must be an object {{supported, not_supported}}")
            continue
        unknown = sorted(set(own) - set(RULE_KEYS))
        if unknown:
            out.append(f"the rules for {k} have {', '.join(unknown)}; an entry has only {', '.join(RULE_KEYS)}")
        if own.get("parameters") is not None and not isinstance(own["parameters"], dict):
            out.append(f"the parameters of the rules for {k} must be an object")
            continue
        eff = effective(decision, k)
        out += [f"rules for {k}: {p}" for p in problems(eff, runs)]
    return out


def _by_run(results):
    """Results as a list per deciding run; a flat list of result bodies counts as one run."""
    if results and all(isinstance(r, list) for r in results):
        return results
    return [list(results)]


def bound_variable(spec):
    """The variable a quantity binds (claimset §3.3.3): `variable`, or `quantity` in records begun before lab record
    v0.20."""
    return spec.get("variable", spec.get("quantity")) if isinstance(spec, dict) else None


def observed(result):
    """What a result observes: its `variable`, or `quantity` in records begun before lab record v0.20."""
    return result.get("variable", result.get("quantity")) if isinstance(result, dict) else None


def bind(decision, results):
    """Bind each quantity to one result. Returns (values, problems).

    `results` holds the result bodies of the deciding runs: a list per run, in order, or a flat list
    for a single run.
    """
    values, probs = {}, []
    per_run = _by_run(results)
    quantities = decision.get("quantities") if isinstance(decision.get("quantities"), dict) else {}
    for name, spec in quantities.items():
        if not isinstance(spec, dict):
            probs.append(f"quantity '{name}' is not well formed")
            continue
        cond = spec.get("conditions") if isinstance(spec.get("conditions"), dict) else {}
        run = spec.get("run")
        pool = per_run[run - 1] if isinstance(run, int) and 1 <= run <= len(per_run) else \
            [r for rs in per_run for r in rs]
        want = bound_variable(spec)
        if "count" in spec:                     # records begun before lab record v0.20
            from ..extensions.qualitative_count import result_variable
            want = result_variable(name)
        matches = [r for r in pool if isinstance(r, dict) and observed(r) == want
                   and all((r.get("conditions") if isinstance(r.get("conditions"), dict) else {}).get(k) == v
                           for k, v in cond.items())]
        if len(matches) != 1:
            where = f"deciding run {run}" if run else "the deciding runs"
            probs.append(f"quantity '{name}' matches {len(matches)} results of {where}, not exactly one")
            continue
        field = spec.get("field", "value")
        r = matches[0]
        v = read_field(r, field)
        if not _is_number(v):
            probs.append(f"quantity '{name}' has no numeric {field}")
            continue
        values[name] = v
    return values, probs


def read_field(result, field):
    """The number a quantity reads from a result: its value, the limit or an end of a value that is a limit or a
    range, a bound of its interval, or the symmetric uncertainty of the one component of a kind (`uncertainty.se`);
    None when there is no such number."""
    if field == "value":
        return result.get("value")
    part, _, key = str(field).partition(".")
    if part == "value":                                       # value.limit, value.min, value.max
        return (result.get("value") if isinstance(result.get("value"), dict) else {}).get(key)
    if part == "interval":
        return (result.get("interval") if isinstance(result.get("interval"), dict) else {}).get(key)
    if part == "uncertainty":
        comps = [u for u in result.get("uncertainty") or [] if isinstance(u, dict) and u.get("kind") == key]
        return comps[0].get("value") if len(comps) == 1 else None
    return None


def verdict(decision, results):
    """The outcome the rule gives on these results.

    Returns {"verdict", "values", "problems", "holds"}; "verdict" is None for text rules.
    """
    if not computed(decision):
        return {"verdict": None, "values": {}, "problems": [], "holds": {}}
    values, probs = bind(decision, results)
    if probs:
        return {"verdict": "inconclusive", "values": values, "problems": probs, "holds": {}}
    env = dict(numeric_parameters(decision), **values)
    holds = {}
    try:
        for outcome in ("supported", "not_supported"):
            holds[outcome] = bool(_eval(parse(decision[outcome], env), env))
    except RuleError as exc:
        return {"verdict": "inconclusive", "values": values, "problems": [str(exc)], "holds": holds}
    if holds["supported"] and holds["not_supported"]:
        return {"verdict": "inconclusive", "values": values, "holds": holds,
                "problems": ["both rules hold: the plan's rules overlap, so the test is inconclusive"]}
    v = "supported" if holds["supported"] else "not_supported" if holds["not_supported"] else "inconclusive"
    return {"verdict": v, "values": values, "problems": [], "holds": holds}
