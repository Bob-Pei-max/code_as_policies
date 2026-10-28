"""Bounded program-to-action interface. Model text is never executed as Python."""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Protocol

COLORS = ('blue', 'red', 'green', 'orange', 'yellow', 'purple', 'pink', 'cyan', 'brown', 'gray')
MAX_SOURCE_BYTES = 16384
MAX_NODES = 1000
MAX_DEPTH = 24
MAX_OPERATIONS = 2000
MAX_VALUE_ITEMS = 32
PURE_CALLS = {'get_obj_names': 0, 'get_color': 1, 'is_block': 1, 'is_bowl': 1}
ACTION_CALL = 'put_first_on_second'
SYSTEM_PROMPT = '''Translate the user's task into a small Python-like robot program.
Return only code, optionally enclosed in one python code fence. Use exact scene names.
Available functions: get_obj_names(), get_color(name), is_block(name), is_bowl(name),
put_first_on_second(block_name, bowl_name). The last function is an action statement.
Supported syntax: string literals, lists/tuples, single-variable assignment, for loops
over lists/tuples, if/else, == and != comparisons, and string concatenation with +.
This is a strict subset of Python. Boolean operators and/or/not, break and continue
are NOT supported. Express combined conditions with nested if statements instead.
Prefer direct action statements using object-name string literals when the task names
the targets. Use loops only when needed; every variable must be assigned before use.
Each block may be picked at most once. Only block-to-bowl placement is supported.
Do not use imports, attributes, subscripts, comprehensions, while, function definitions,
arbitrary coordinates, or any other calls. No helper function generation is available.
If the task cannot be expressed with the available objects and actions, return no actions.
'''


class PlanError(ValueError):
    """A scene, program, or expanded action violates the supported contract."""


@dataclass(frozen=True)
class Action:
    pick: str
    place: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ProgramReply:
    text: str
    model_api_used: bool
    metadata: dict


class ProgramProvider(Protocol):
    """A program source returns text plus non-secret provenance."""

    def generate(self, messages: list[dict[str, str]]) -> ProgramReply: ...


class FileProgramProvider:
    """Replay supplied code; does NOT interpret the task or call a language model."""

    def __init__(self, path: Path):
        self.path = path

    def generate(self, messages: list[dict[str, str]]) -> ProgramReply:
        with self.path.open('rb') as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise PlanError('Program exceeds source byte limit')
        return ProgramReply(data.decode('utf-8-sig'), False, {
            'provider': 'file_replay', 'file_name': self.path.name,
            'sha256': hashlib.sha256(data).hexdigest(), 'model': None,
            'task_interpreted_by_model': False,
        })


def validate_objects(objects):
    if not isinstance(objects, list) or not 2 <= len(objects) <= 4:
        raise PlanError('Scene must contain 2 to 4 objects')
    valid = {f'{color} {kind}' for color in COLORS for kind in ('block', 'bowl')}
    if any(not isinstance(name, str) or name not in valid for name in objects):
        raise PlanError('Scene contains an unsupported object name')
    if len(set(objects)) != len(objects):
        raise PlanError('Scene object names must be unique')
    if not any(name.endswith(' block') for name in objects) or not any(name.endswith(' bowl') for name in objects):
        raise PlanError('Scene needs at least one block and one bowl')


def validate_action(pick, place, objects):
    if not isinstance(pick, str) or pick not in objects or not pick.endswith(' block'):
        raise PlanError('Pick must name a scene block')
    if not isinstance(place, str) or place not in objects or not place.endswith(' bowl'):
        raise PlanError('Place must name a scene bowl')
    return Action(pick, place)


def load_scene(path: Path):
    scene = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(scene, dict) or set(scene) - {'objects', 'goals'}:
        raise PlanError('Scene accepts only objects and optional goals')
    validate_objects(scene.get('objects'))
    goals = scene.get('goals', [])
    if not isinstance(goals, list) or len(goals) > 4:
        raise PlanError('Goals must be a list of at most 4 pick/place pairs')
    seen = set()
    for goal in goals:
        if not isinstance(goal, dict) or set(goal) != {'pick', 'place'}:
            raise PlanError('Each goal must contain pick and place')
        action = validate_action(goal['pick'], goal['place'], scene['objects'])
        if action.pick in seen:
            raise PlanError('Each block may have only one goal')
        seen.add(action.pick)
    return {'objects': scene['objects'], 'goals': goals}


def build_messages(task, objects, max_actions=4):
    validate_objects(objects)
    if not task.strip() or len(task) > 4096:
        raise PlanError('Task must contain 1 to 4096 characters')
    return [
        {'role': 'system', 'content': SYSTEM_PROMPT + f'Action limit: {max_actions}.\n'},
        {'role': 'user', 'content': json.dumps({'task': task, 'objects': objects}, ensure_ascii=False)},
    ]


def normalize_program(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise PlanError('Program exceeds source byte limit')
    text = text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0] not in ('```', '```python') or lines[-1] != '```':
            raise PlanError('Expected a single complete Python code fence')
        text = '\n'.join(lines[1:-1])
    return text + '\n'


class _Compiler:
    """Interpret a closed AST vocabulary into data, with no Python exec/eval."""

    allowed = (ast.Module, ast.Assign, ast.Expr, ast.For, ast.If, ast.Name,
               ast.Constant, ast.List, ast.Tuple, ast.Call, ast.BinOp, ast.Add,
               ast.Compare, ast.Eq, ast.NotEq, ast.Load, ast.Store)

    def __init__(self, objects, max_actions):
        self.objects = tuple(objects)
        self.max_actions = max_actions
        self.variables = {}
        self.actions = []
        self.operations = 0

    def validate_tree(self, tree):
        pending = [(tree, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > MAX_NODES or depth > MAX_DEPTH:
                raise PlanError('Program exceeds AST size/depth limit')
            if not isinstance(node, self.allowed):
                raise PlanError(f'Unsupported syntax: {type(node).__name__}')
            if isinstance(node, ast.Name) and node.id.startswith('_'):
                raise PlanError('Private names are not allowed')
            if isinstance(node, ast.Constant) and (type(node.value) is not str or len(node.value) > 128):
                raise PlanError('Only string literals of at most 128 characters are allowed')
            if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) > MAX_VALUE_ITEMS:
                raise PlanError('Collection exceeds item limit')
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    raise PlanError('Only single-variable assignment is supported')
                self.validate_target(node.targets[0])
            if isinstance(node, ast.For):
                self.validate_target(node.target)
                if node.orelse:
                    raise PlanError('for/else is not supported')
            if isinstance(node, ast.Compare) and len(node.ops) != 1:
                raise PlanError('Only single comparisons are supported')
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.keywords:
                    raise PlanError('Only named API calls without keywords are supported')
                arities = {**PURE_CALLS, ACTION_CALL: 2}
                if node.func.id not in arities or len(node.args) != arities[node.func.id]:
                    raise PlanError('Unknown API call or wrong number of arguments')
                if node.func.id == ACTION_CALL and not any(
                    isinstance(parent, ast.Expr) and parent.value is node for parent in ast.walk(tree)
                ):
                    raise PlanError('Actions must be standalone statements')
            pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))

    def validate_target(self, node):
        if not isinstance(node, ast.Name) or node.id.startswith('_') or node.id in {*PURE_CALLS, ACTION_CALL}:
            raise PlanError('Assignment/loop target must be a non-API variable name')

    def tick(self):
        self.operations += 1
        if self.operations > MAX_OPERATIONS:
            raise PlanError('Program exceeds interpretation budget')

    def expression(self, node):
        self.tick()
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.variables:
                raise PlanError(f'Undefined variable: {node.id}')
            return self.variables[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return tuple(self.expression(item) for item in node.elts)
        if isinstance(node, ast.BinOp):
            left, right = self.expression(node.left), self.expression(node.right)
            if not isinstance(left, str) or not isinstance(right, str) or len(left) + len(right) > 128:
                raise PlanError('Only bounded string concatenation is supported')
            return left + right
        if isinstance(node, ast.Compare):
            left, right = self.expression(node.left), self.expression(node.comparators[0])
            if not isinstance(left, (str, bool)) or type(left) is not type(right):
                raise PlanError('Compare strings or booleans of the same type')
            return left == right if isinstance(node.ops[0], ast.Eq) else left != right
        if isinstance(node, ast.Call):
            name = node.func.id
            if name == 'get_obj_names':
                return self.objects
            args = [self.expression(arg) for arg in node.args]
            if name not in PURE_CALLS or not isinstance(args[0], str) or args[0] not in self.objects:
                raise PlanError('Query must reference a scene object')
            if name == 'get_color':
                return args[0].split()[0]
            return args[0].endswith(' block' if name == 'is_block' else ' bowl')
        raise PlanError('Unsupported expression')

    def statements(self, statements):
        for node in statements:
            self.tick()
            if isinstance(node, ast.Assign):
                self.variables[node.targets[0].id] = self.expression(node.value)
            elif isinstance(node, ast.For):
                values = self.expression(node.iter)
                if not isinstance(values, tuple):
                    raise PlanError('for requires a list/tuple, not a string')
                for value in values:
                    self.variables[node.target.id] = value
                    self.statements(node.body)
            elif isinstance(node, ast.If):
                condition = self.expression(node.test)
                if type(condition) is not bool:
                    raise PlanError('if requires a boolean query or comparison')
                self.statements(node.body if condition else node.orelse)
            elif isinstance(node, ast.Expr):
                call = node.value
                if not isinstance(call, ast.Call) or call.func.id != ACTION_CALL:
                    raise PlanError('Only action calls are supported as expression statements')
                pick, place = (self.expression(arg) for arg in call.args)
                action = validate_action(pick, place, self.objects)
                if len(self.actions) >= self.max_actions:
                    raise PlanError('Program exceeds action limit')
                if any(previous.pick == pick for previous in self.actions):
                    raise PlanError('Repeated picks are not supported')
                self.actions.append(action)
            else:
                raise PlanError('Unsupported statement')


def compile_program(source, objects, max_actions=4):
    """Validate the entire program, then return a finite immutable action sequence."""
    validate_objects(objects)
    if type(max_actions) is not int or not 1 <= max_actions <= 4:
        raise PlanError('Action limit must be between 1 and 4')
    source = normalize_program(source)
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise PlanError('Invalid program syntax') from exc
    compiler = _Compiler(objects, max_actions)
    compiler.validate_tree(tree)
    compiler.statements(tree.body)
    if not compiler.actions:
        raise PlanError('Program contains no actions')
    return tuple(compiler.actions)
