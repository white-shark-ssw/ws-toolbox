"""Position-preserving Lua 5.1 lexer and lexical-binding parser.

This is deliberately not a formatter: only proven local identifier spans and
comment spans may be edited. Unknown syntax is rejected, never guessed.
"""
from dataclasses import dataclass, field
import re
import secrets

RULE_VERSION = 'lua51-local-bindings-v1'
KEYWORDS = set('and break do else elseif end false for function if in local nil not or repeat return then true until while'.split())
NAME = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
NUMBER = re.compile(r'(?:0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)')
NEWLINES = re.compile(r'\r\n|\r|\n')


class LuaSyntaxError(ValueError):
    pass


@dataclass
class Token:
    kind: str
    text: str
    start: int
    end: int
    line: int
    role: str = ''
    binding: int | None = None


@dataclass
class Binding:
    index: int
    name: str
    implicit: bool = False
    tokens: list[int] = field(default_factory=list)


def lex(source):
    tokens, comments = [], []
    pos, line, length = 0, 1, len(source)

    def long_end(start):
        opening = re.match(r'\[(=*)\[', source[start:])
        if opening is None:
            return None
        closing = ']' + opening.group(1) + ']'
        end = source.find(closing, start + len(opening.group()))
        if end < 0:
            raise LuaSyntaxError(f'line {line}: unfinished long string/comment')
        return end + len(closing)

    while pos < length:
        start, original_line = pos, line
        char = source[pos]
        kind = None
        if char.isspace() and char in ' \t\v\f\r\n':
            pos += 1
            while pos < length and source[pos] in ' \t\v\f\r\n':
                pos += 1
        elif source.startswith('--', pos):
            pos += 2
            ending = long_end(pos)
            if ending is not None:
                pos = ending
            else:
                while pos < length and source[pos] not in '\r\n':
                    pos += 1
            kind = 'comment'
        elif char in ('"', "'"):
            quote = char
            pos += 1
            while pos < length:
                char = source[pos]
                if char == '\\':
                    pos += 1
                    if pos < length and source[pos] == '\r' and source[pos:pos + 2] == '\r\n':
                        pos += 2
                    elif pos < length:
                        pos += 1
                    else:
                        raise LuaSyntaxError(f'line {original_line}: unfinished escape')
                elif char == quote:
                    pos += 1
                    break
                elif char in '\r\n':
                    raise LuaSyntaxError(f'line {original_line}: newline in quoted string')
                else:
                    pos += 1
            else:
                raise LuaSyntaxError(f'line {original_line}: unfinished quoted string')
            kind = 'string'
        elif char == '[' and (ending := long_end(pos)) is not None:
            pos = ending
            kind = 'string'
        elif match := NAME.match(source, pos):
            pos = match.end()
            kind = 'keyword' if match.group() in KEYWORDS else 'name'
        elif match := NUMBER.match(source, pos):
            pos = match.end()
            kind = 'number'
        else:
            operator = next((x for x in ('...', '..', '==', '~=', '<=', '>=') if source.startswith(x, pos)), None)
            if operator:
                pos += len(operator)
            elif char in '+-*/%^#=<>;:,().{}[]':
                pos += 1
            else:
                raise LuaSyntaxError(f'line {line}: unsupported character/syntax {char!r}')
            kind = 'symbol'
        fragment = source[start:pos]
        line += len(NEWLINES.findall(fragment))
        if kind:
            token = Token(kind, fragment, start, pos, original_line)
            (comments if kind == 'comment' else tokens).append(token)
    tokens.append(Token('eof', '<eof>', length, length, line))
    return tokens, comments


class Parser:
    """Resolve every identifier to a declaration, global access or member key."""
    BINARY = {'or': (1, False), 'and': (2, False), '<': (3, False), '>': (3, False),
              '<=': (3, False), '>=': (3, False), '~=': (3, False), '==': (3, False),
              '..': (4, True), '+': (5, False), '-': (5, False), '*': (6, False),
              '/': (6, False), '%': (6, False), '^': (8, True)}

    def __init__(self, source):
        self.source = source
        self.tokens, self.comments = lex(source)
        self.pos = 0
        self.scopes = [{}]
        self.bindings = []

    @property
    def token(self):
        return self.tokens[self.pos]

    def error(self, message):
        raise LuaSyntaxError(f'line {self.token.line}: {message}; found {self.token.text!r}')

    def take(self, value=None):
        if value is not None and self.token.text != value:
            self.error('expected ' + value)
        index = self.pos
        self.pos += 1
        return index

    def accept(self, value):
        if self.token.text == value:
            self.take()
            return True
        return False

    def identifier(self, field=False):
        if self.token.kind != 'name':
            self.error('expected identifier')
        index = self.take()
        token = self.tokens[index]
        if field:
            token.role = 'field'
        else:
            token.role = 'global'
            for scope in reversed(self.scopes):
                if token.text in scope:
                    binding = self.bindings[scope[token.text]]
                    token.role, token.binding = 'reference', binding.index
                    binding.tokens.append(index)
                    break
        return index

    def unbound_name(self):
        if self.token.kind != 'name':
            self.error('expected local name')
        return self.take()

    def declare(self, index=None, implicit_name=None):
        name = implicit_name if index is None else self.tokens[index].text
        binding = Binding(len(self.bindings), name, index is None)
        self.bindings.append(binding)
        self.scopes[-1][name] = binding.index
        if index is not None:
            self.tokens[index].role = 'declaration'
            self.tokens[index].binding = binding.index
            binding.tokens.append(index)
        return binding

    def parse(self):
        self.block({'<eof>'})
        self.take('<eof>')
        for token in self.tokens:
            if token.kind == 'name' and not token.role:
                raise LuaSyntaxError(f'line {token.line}: unresolved identifier {token.text}')
        return self

    def nested_block(self, stops):
        self.scopes.append({})
        self.block(stops)
        self.scopes.pop()

    def block(self, stops):
        while self.token.text not in stops:
            if self.token.kind == 'eof':
                self.error('unexpected end of source')
            self.statement()

    def statement(self):
        word = self.token.text
        if self.accept(';'):
            return
        if self.accept('local'):
            if self.accept('function'):
                self.declare(self.unbound_name())
                self.function_body()
                return
            names = [self.unbound_name()]
            while self.accept(','):
                names.append(self.unbound_name())
            if self.accept('='):
                self.expressions()
            # All initializers see outer declarations, not the names above.
            for index in names:
                self.declare(index)
        elif self.accept('function'):
            self.identifier()
            while self.accept('.'):
                self.identifier(field=True)
            method = self.accept(':')
            if method:
                self.identifier(field=True)
            self.function_body(method)
        elif self.accept('do'):
            self.nested_block({'end'})
            self.take('end')
        elif self.accept('while'):
            self.expression()
            self.take('do')
            self.nested_block({'end'})
            self.take('end')
        elif self.accept('repeat'):
            self.scopes.append({})
            self.block({'until'})
            self.take('until')
            self.expression()
            self.scopes.pop()
        elif self.accept('if'):
            self.expression()
            self.take('then')
            self.nested_block({'elseif', 'else', 'end'})
            while self.accept('elseif'):
                self.expression()
                self.take('then')
                self.nested_block({'elseif', 'else', 'end'})
            if self.accept('else'):
                self.nested_block({'end'})
            self.take('end')
        elif self.accept('for'):
            names = [self.unbound_name()]
            if self.accept('='):
                self.expression()
                self.take(',')
                self.expression()
                if self.accept(','):
                    self.expression()
            else:
                while self.accept(','):
                    names.append(self.unbound_name())
                self.take('in')
                self.expressions()
            self.take('do')
            self.scopes.append({})
            for index in names:
                self.declare(index)
            self.block({'end'})
            self.take('end')
            self.scopes.pop()
        elif self.accept('return'):
            if self.token.text not in ('end', 'else', 'elseif', 'until', ';', '<eof>'):
                self.expressions()
            self.accept(';')
        elif self.accept('break'):
            pass
        elif word in ('end', 'else', 'elseif', 'until', 'then', 'in'):
            self.error('unexpected block terminator')
        else:
            assignable, called = self.prefix()
            if self.token.text in (',', '='):
                if not assignable:
                    self.error('assignment target is not a variable')
                while self.accept(','):
                    if not self.prefix()[0]:
                        self.error('assignment target is not a variable')
                self.take('=')
                self.expressions()
            elif not called:
                self.error('expected assignment or function call')

    def function_body(self, method=False):
        self.take('(')
        self.scopes.append({})
        if method:
            self.declare(implicit_name='self')
        params, varargs = [], False
        if not self.accept(')'):
            while True:
                if self.accept('...'):
                    varargs = True
                    break
                params.append(self.unbound_name())
                if not self.accept(','):
                    break
            self.take(')')
        if varargs:
            self.declare(implicit_name='arg')
        for index in params:
            self.declare(index)
        self.block({'end'})
        self.take('end')
        self.scopes.pop()

    def expressions(self):
        self.expression()
        while self.accept(','):
            self.expression()

    def expression(self, minimum=0):
        if self.token.text in ('not', '-', '#'):
            self.take()
            self.expression(7)
        elif self.token.text in ('nil', 'true', 'false', '...') or self.token.kind in ('number', 'string'):
            self.take()
        elif self.accept('function'):
            self.function_body()
        elif self.token.text == '{':
            self.table()
        else:
            self.prefix()
        while self.token.text in self.BINARY:
            power, right_associative = self.BINARY[self.token.text]
            if power < minimum:
                break
            self.take()
            self.expression(power if right_associative else power + 1)

    def prefix(self):
        if self.token.kind == 'name':
            self.identifier()
            assignable = True
        elif self.accept('('):
            self.expression()
            self.take(')')
            assignable = False
        else:
            self.error('expected expression')
        called = False
        while True:
            if self.accept('.'):
                self.identifier(field=True)
                assignable, called = True, False
            elif self.accept('['):
                self.expression()
                self.take(']')
                assignable, called = True, False
            elif self.accept(':'):
                self.identifier(field=True)
                self.arguments(required=True)
                assignable, called = False, True
            elif self.arguments():
                assignable, called = False, True
            else:
                return assignable, called

    def arguments(self, required=False):
        if self.accept('('):
            if not self.accept(')'):
                self.expressions()
                self.take(')')
        elif self.token.text == '{':
            self.table()
        elif self.token.kind == 'string':
            self.take()
        elif required:
            self.error('expected call arguments')
        else:
            return False
        return True

    def table(self):
        self.take('{')
        while not self.accept('}'):
            if self.accept('['):
                self.expression()
                self.take(']')
                self.take('=')
                self.expression()
            elif self.token.kind == 'name' and self.tokens[self.pos + 1].text == '=':
                self.identifier(field=True)
                self.take('=')
                self.expression()
            else:
                self.expression()
            if not self.accept(',') and not self.accept(';'):
                self.take('}')
                break


def check_reflection(parsed):
    """Reject name-sensitive debug access and opaque aliases of the debug table.

    This is a conservative supported-input contract, not a proof about arbitrary
    dynamically generated programs. It intentionally rejects ambiguous cases.
    """
    tokens = parsed.tokens
    forbidden = {'getlocal', 'setlocal', 'getupvalue', 'setupvalue', 'upvalueid', 'upvaluejoin'}
    # Also reject simple constant-concatenation spellings of reflective keys;
    # an opaque alias is not made safe merely by splitting its string literal.
    for index, token in enumerate(tokens[:-1]):
        if token.kind == 'string' and token.text[:1] in ('"', "'") and '\\' not in token.text:
            value, cursor = token.text[1:-1], index + 1
            while cursor + 1 < len(tokens) and tokens[cursor].text == '..' and tokens[cursor + 1].kind == 'string':
                literal = tokens[cursor + 1].text
                if literal[:1] not in ('"', "'") or '\\' in literal:
                    break
                value += literal[1:-1]
                cursor += 2
            if value == 'debug' or value in forbidden:
                raise LuaSyntaxError(f'line {token.line}: dynamic reflection key is unsupported')
    for index, token in enumerate(tokens[:-1]):
        if token.kind == 'name' and token.text in forbidden:
            raise LuaSyntaxError(f'line {token.line}: name-sensitive reflection {token.text} is unsupported')
        if token.kind == 'string' and any(name in token.text for name in forbidden):
            raise LuaSyntaxError(f'line {token.line}: possible dynamic local/upvalue reflection is unsupported')
        if token.kind == 'name' and token.text == 'debug':
            following = tokens[index + 1:index + 3]
            # A direct existence guard is not an alias or a reflective call.
            if index > 0 and tokens[index - 1].text == 'if' and following[0].text in ('and', 'then'):
                continue
            if len(following) != 2 or following[0].text != '.' or following[1].text not in ('getinfo', 'traceback'):
                raise LuaSyntaxError(f'line {token.line}: indirect/unsupported debug access is not safe to rename')


def transform(source, strip_comments, obfuscate):
    parsed = Parser(source).parse()
    replacements = {}
    mapping = []
    if obfuscate:
        check_reflection(parsed)
        reserved = {token.text for token in parsed.tokens if token.kind == 'name'} | KEYWORDS
        prefix = '_' + secrets.token_hex(1)
        for binding in parsed.bindings:
            # Lua 5.1 compatibility vararg 'arg' and implicit self stay untouched.
            if binding.implicit or binding.name == 'arg':
                continue
            name = prefix + format(binding.index, 'x')
            while name in reserved:
                name += '_'
            reserved.add(name)
            for index in binding.tokens:
                replacements[index] = name
            mapping.append({'binding': binding.index, 'original': binding.name, 'renamed': name})
    edits = [(parsed.tokens[index].start, parsed.tokens[index].end, name) for index, name in replacements.items()]
    if strip_comments:
        for comment in parsed.comments:
            edits.append((comment.start, comment.end, ''.join(c if c in '\r\n' else ' ' for c in comment.text)))
    output = source
    end_boundary = len(source)
    for start, end, replacement in sorted(edits, reverse=True):
        if end > end_boundary:
            raise LuaSyntaxError('overlapping source edits')
        output = output[:start] + replacement + output[end:]
        end_boundary = start
    after = Parser(output).parse()
    if len(parsed.tokens) != len(after.tokens) or NEWLINES.findall(source) != NEWLINES.findall(output):
        raise LuaSyntaxError('token count or physical newline invariant failed')
    for index, (before, changed) in enumerate(zip(parsed.tokens, after.tokens)):
        expected = replacements.get(index, before.text)
        if (before.kind, expected, before.line, before.role, before.binding) != (changed.kind, changed.text, changed.line, changed.role, changed.binding):
            raise LuaSyntaxError(f'line {before.line}: token/binding invariant failed')
    if not strip_comments and [x.text for x in parsed.comments] != [x.text for x in after.comments]:
        raise LuaSyntaxError('comment preservation invariant failed')
    if strip_comments and after.comments:
        raise LuaSyntaxError('comment removal invariant failed')
    return output, mapping
