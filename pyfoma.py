import heapq, operator, itertools, re, functools
from collections import deque, defaultdict


class regexparse:

    shortops = {'|':'UNION', '-':'MINUS', '&':'INTERSECTION', '*':'STAR', '+':'PLUS',
                '(':'LPAREN', ')':'RPAREN', '?':'OPTIONAL', ':':'CP', '~':"COMPLEMENT",
                '@':"COMPOSE", ',': 'COMMA'}
    builtins = {'reverse': lambda x: FST.reverse(x), 'invert':lambda x: FST.invert(x),
                'minimize': lambda x: FST.minimize(x), 'determinize': lambda x:\
                FST.determinize(x), 'ignore': lambda x,y: FST.ignore(x,y)}
    precedence = {"FUNC": 11, "CONTAINS":11, "COMMA":2, "COMPOSE":3, "UNION":4,
                  "INTERSECTION":4, "MINUS":4, "CONCAT":6, "IGNORE":7, "COMPLEMENT":8,
                  "STAR":9, "PLUS":9, "OPTIONAL":9, "WEIGHT":9, "CP":10, "RANGE":9}
    operands  = {"SYMBOL", "VARIABLE", "ANY", "EPSILON", "CHAR_CLASS"}
    operators = set(precedence.keys())
    unarypost = {"STAR", "PLUS", "WEIGHT", "OPTIONAL", "RANGE"}
    unarypre  = {"COMPLEMENT"}

    def __init__(self, regularexpression, defined, functions):
        """Tokenize, parse, and compile regex into FST.
        'I define UNIX as 30 definitions of regular expressions living under one roof.'
        - Don Knuth, Digital Typography, ch. 33, p. 649 (1999)"""

        self.defined = defined
        self.functions = {f.__name__:f for f in functions} # Who you gonna call?
        self.expression = regularexpression
        self.tokenized = self._addconc(self.tokenize())
        self.parsed = self.parse(self.tokenized)
        self.compiled = self.compile()

    def character_class_parse(self, charclass):
        """Parse a character class into range pairs, e.g. a-zA => [(97,122), (65,65)].
           'Writing clear and unambiguous specifications for character classes is tough,
           and implementing them perfectly is worse, requiring a lot of tedious and
           uninstructive coding.' -Brian Kernighan (in "Beautiful Code", 2007). """

        negated = False
        if charclass[0] == '^':
            negated = True
            charclass = charclass[1:]

        clncc, escaped = [], set()
        j = 0
        for letter in charclass: # remove escape chars and index those escaped positions
            if letter != '\\':
                clncc.append(letter)
                j += 1
            else:
                escaped.add(j)
        # Mark positions with a range (i.e. mark where the "-"-symbol is)
        marks = [True if (clncc[i] == '-' and i not in escaped and i != 0 and \
                      i != len(clncc)-1) else False for i in range(len(clncc))]
        ranges = [(ord(clncc[i-1]), ord(clncc[i+1])) for i in range(len(clncc)) if marks[i]]

        # 3-convolve over marks to figure out where the non-range characters are
        singles = [any(m) for m in zip(marks, marks[1:] + [False], [False] + marks)]
        for i in range(len(singles)):
            if singles[i] == False:
                ranges.append((ord(clncc[i]), ord(clncc[i]))) # dummy range, e.g (65, 65)

        if any(start > end for start, end in ranges):
            raise SyntaxError("End must be larger than start in character class range.")
        return ranges, negated

    def compile(self):
        def _stackcheck(s):
            if not s:
                self._error_report(SyntaxError, "You stopped making sense!", line_num, column)
            return s
        def _pop(s):
            return _stackcheck(s).pop()[0]
        def _peek(s): # For unaries we just directly mutate the FSM on top of the stack
            return _stackcheck(s)[-1][0]
        def _append(s, element):
            s.append([element])
        def _merge(s):     # since we keep the FSTs inside lists, we need to do some`
            _stackcheck(s) # reshuffling with a COMMA token so that the top 2 elements
            one = s.pop()  # get merged into one list that ends up on top of the stack.
            _stackcheck(s) # [[FST1], [FST2], [FST3]] => [[FST1], [FST2, FST3]]
            s.append(s.pop() + one)
        def _getargs(s):
            return _stackcheck(s).pop()
        stack = []
        for op, value, line_num, column in self.parsed:
            if op == 'FUNC':
                if value in self.functions:
                    _append(stack, self.functions[value](*_getargs(stack)))
                elif value in self.builtins:
                    _append(stack, self.builtins[value](*_getargs(stack)))
                else:
                    self._error_report(SyntaxError, "Function \"" + value + "\" not defined.", line_num, column)
            if op == 'LPAREN':
                self._error_report(SyntaxError, "Missing closing parentehesis.", line_num, column)
            if op == 'COMMA': # Create tuple on top of stack of top two elements
                _merge(stack)
            if op == 'UNION':
                _append(stack, _pop(stack).union(_pop(stack)))
            if op == 'MINUS':
                arg2, arg1 = _pop(stack), _pop(stack)
                _append(stack, arg1.difference(arg2.determinize()))
            elif op == 'INTERSECTION':
                _append(stack, _pop(stack).intersection(_pop(stack)).coaccessible())
            elif op == 'CONCAT':
                second = _pop(stack)
                _append(stack, _pop(stack).concatenate(second).accessible())
            elif op == 'CONTAINS':
                _append(stack, FST(label = '.').kleene_closure().concatenate(_pop(stack)).concatenate(FST(label = '.').kleene_closure()))
            elif op == 'STAR':
                _append(stack, _pop(stack).kleene_closure())
            elif op == 'PLUS':
                _append(stack, _pop(stack).kleene_closure(mode = 'plus'))
            elif op == 'COMPOSE':
                arg2, arg1 = _pop(stack), _pop(stack)
                _append(stack, arg1.compose(arg2).coaccessible())
            elif op == 'OPTIONAL':
                _peek(stack).optional()
            elif op == 'RANGE':
                rng = value.split(',')
                lang = _pop(stack)
                if len(rng) == 1:  # e.g. {3}
                    _append(stack, functools.reduce(lambda x, y: x.concatenate(y), [lang]*int(value)))
                elif rng[0] == '': # e.g. {,3}
                    lang = lang.optional()
                    _append(stack, functools.reduce(lambda x, y: x.concatenate(y), [lang]*int(rng[1])))
                elif rng[1] == '': # e.g. {3,}
                    _append(stack, functools.reduce(lambda x, y: x.concatenate(y), [lang]*int(rng[0])).concatenate(lang.kleene_closure()))
                else:              # e.g. {1,4}
                    if int(rng[0] > rng[1]):
                        self._error_report(SyntaxError, "n must be greater than m in {m,n}", line_num, column)
                    lang1 = functools.reduce(lambda x, y: x.concatenate(y), [lang]*int(rng[0]))
                    lang2 = functools.reduce(lambda x, y: x.concatenate(y), [lang.optional()]*(int(rng[1])-int(rng[0])))
                    _append(stack, lang1.concatenate(lang2))
            elif op == 'CP':
                arg2, arg1 = _pop(stack), _pop(stack)
                _append(stack, arg1.cross_product(arg2).coaccessible())
            elif op == 'WEIGHT':
                _peek(stack).add_weight(float(value)).push_weights()
            elif op == 'SYMBOL':
                _append(stack, FST(label = (value,)))
            elif op == 'ANY':
                _append(stack, FST(label = ('.',)))
            elif op == 'VARIABLE': # TODO: copy self.defined[value]
                if value not in self.defined:
                    self._error_report(SyntaxError, "Defined FST \"" + value + "\" not found.", line_num, column)
                _append(stack, self.defined[value])
            elif op == 'CHAR_CLASS':
                charranges, negated = self.character_class_parse(value)
                _append(stack, FST.character_ranges(charranges, complement = negated))
        if len(stack) != 1: # If there's still stuff on the stack, that's a syntax error
            self._error_report(SyntaxError, "Something's happening here, and what it is ain't exactly clear...", 1, 0)
        return _pop(stack).trim().push_weights().minimize()

    def tokenize(self):
        """Token, token, token, though the stream is broken... ride 'em in, tokenize!"""
        # prematch (skip this), groupname, core regex (capture), postmatch (skip)
        token_regexes = [
        (r'\\'  , 'ESCAPED',    r'.',                        r''),          # Esc'd sym
        (r"'"   , 'QUOTED',     r"(\\[']|[^'])*",            r"'"),         # Quoted sym
        (r''    , 'SKIPWS',     r'([ \t]+)',                 r''),          # Skip ws
        (r''    , 'SHORTOP',    r'([|\-&*+()?:~@,])',        r''),          # len 1 ops
        (r'\$\^', 'FUNC',       r'(\w+)',                    r'(?=\s*\()'), # Functions
        (r'\$'  , 'VARIABLE',   r'(\w+)',                    r''),          # Variables
        (r'<'   , 'WEIGHT',     r'([+-]?[0-9]*(\.[0-9]+)?)', r'>'),         # Weight
        (r'\{'  , 'RANGE',      r'(\d+,(\d+)?|,?\d+|\d+)',   r'\}'),        # {(m),(n)}
        (r'\['  , 'CHAR_CLASS', r'((\]|[^]\[])+)',           r'\]'),        # Char class
        (r''    , 'NEWLINE',    r'(\n)',                     r''),          # Line end
        (r''    , 'SYMBOL',     r'(.)',                      r'')           # Single sym
    ]
        tok_regex = '|'.join('%s(?P<%s>%s)%s' % mtch for mtch in token_regexes)
        line_num, line_start, res = 1, 0, []
        for mo in re.finditer(tok_regex, self.expression):
            op = mo.lastgroup
            value = mo.group(op)
            column = mo.start() - line_start
            if op == 'SKIPWS':
                continue
            elif op == 'ESCAPED' or op == 'QUOTED':
                op = 'SYMBOL'
            elif op == 'NEWLINE':
                line_start = mo.end()
                line_num += 1
                continue
            elif op == 'SHORTOP':
                op = self.shortops[value]
            res.append((op, value, line_num, column))
        return res

    def _addconc(self, tokens):
        """Idiot hack or genius? We insert explicit CONCAT tokens before parsing.

           'I now avoid invisible infix operators almost entirely. I do remember a few
           texts dealing with theorems about strings in which concatenation was denoted
           by juxtaposition.' (EWD 1300-9)"""

        resetters = self.operators - self.unarypost
        counter, result = 0, []
        for token, value, line_num, column in tokens: # It's a two-state FST!
            if counter == 1 and token in {"LPAREN", "COMPLEMENT"} | self.operands:
                result.append(("CONCAT", '', line_num, column))
                counter = 0
            if token in self.operands:
                counter = 1
            if token in resetters: # No, really, it is!
                counter = 0
            result.append((token, value, line_num, column))
        return result

    def _error_report(self, errortype, errorstring, line_num, column):
        raise errortype(errorstring, ("", line_num, column, self.expression))

    def parse(self, tokens):
        """Attention! Those who don't speak reverse Polish will be shunted!
        'Simplicity is a great virtue but it requires hard work to achieve it and
        education to appreciate it. And to make matters worse: complexity sells better.'
        - E. Dijkstra """
        output, stack = [], []
        for token, value, line_num, column in tokens:
            if token in self.operands or token in self.unarypost:
                output.append((token, value, line_num, column))
            elif token in self.unarypre or token == "FUNC" or token == "LPAREN":
                stack.append((token, value, line_num, column))
            elif token == "RPAREN":
                while True:
                    if not stack:
                        self._error_report(SyntaxError, "Too many closing parentheses.", line_num, column)
                    if stack[-1][0] == 'LPAREN':
                        break
                    output.append(stack.pop())
                stack.pop()
                if stack and stack[-1][0] == "FUNC":
                    output.append(stack.pop())
            elif token in self.operators: # We don't have any binaries that assoc right.
                while stack and stack[-1][0] in self.operators and \
                      self.precedence[stack[-1][0]] >= self.precedence[token]:
                    output.append(stack.pop())
                stack.append((token, value, line_num, column))
        while stack:
            output.append(stack.pop())
        return output


class FST:

    @classmethod
    def character_ranges(cls, ranges, complement = False):
        """Returns a two-state FSM from a list of unicode code point range pairs."""
        newfst = cls()
        secondstate = State()
        newfst.states.add(secondstate)
        newfst.finalstates = {secondstate}
        secondstate.finalweight = 0.0
        alphabet = set()
        for start, end in ranges:
            for symbol in range(start, end + 1):
                if symbol not in alphabet:
                    alphabet |= {chr(symbol)}
                    if not complement:
                        newfst.initialstate.add_transition(secondstate, (chr(symbol),), 0.0)
        if complement:
            newfst.initialstate.add_transition(secondstate, ('.',), 0.0)
        newfst.alphabet = alphabet
        return newfst

    @classmethod
    def regex(cls, regularexpression, defined = {}, functions = set()):
        """Compile a regular expression and return the resulting FST."""
        myregex = regexparse(regularexpression, defined, functions)
        return myregex.compiled

    @classmethod
    def rlg(cls, grammar, startsymbol):
        """Compile a (wighted) right-linear grammar into an FST, similarly to lexc."""
        def _rlg_tokenize(w):
            tokens = []
            tok_re = r"'(?P<multi>([']|[^']*))'|\\(?P<esc>(.))|(?P<single>(.))"
            for mo in re.finditer(tok_re, w):
                token = mo.group(mo.lastgroup)
                if token == " " and mo.lastgroup == 'single':
                    token = ""  # normal spaces for alignment, escaped for actual
                tokens.append(token)
            return tokens

        newfst = FST(alphabet = set())
        statedict = {name:State(name = name) for name in grammar.keys() | {"#"}}
        newfst.initialstate = statedict[startsymbol]
        newfst.finalstates = {statedict["#"]}
        statedict["#"].finalweight = 0.0
        newfst.states = set(statedict.values())

        for bigstate in statedict.keys() - {"#"}:
            for rule in grammar[bigstate]:
                currstate = statedict[bigstate]
                lhs = (rule[0],) if isinstance(rule[0], str) else rule[0]
                target = rule[1]
                i = _rlg_tokenize(lhs[0])
                o = i if len(lhs) == 1 else _rlg_tokenize(lhs[1])
                newfst.alphabet |= {sym for sym in i + o}
                for ii, oo, idx in itertools.zip_longest(i, o, range(max(len(i), len(o))), fillvalue = ''):
                    w = 0.0
                    if idx == max(len(i), len(o)) - 1: # dump weight on last transition
                        targetstate = statedict[target]
                        w = 0.0 if len(rule) < 3 else float(rule[2])
                    else:
                        targetstate = State()
                        newfst.states.add(targetstate)
                    currstate.add_transition(targetstate, (ii, oo), w)
                    currstate = targetstate
        return newfst

    def __init__(self, label = None, weight = 0.0, alphabet = set()):

        self.alphabet = alphabet
        self.initialstate = State()
        self.states = {self.initialstate}
        self.finalstates = set()
        if label == ('',): # EPSILON
            self.finalstates.add(self.initialstate)
            self.initialstate.finalweight = weight
        elif label is not None:
            self.alphabet = {s for s in label}
            targetstate = State()
            self.states.add(targetstate)
            self.finalstates = {targetstate}
            targetstate.finalweight = weight
            self.initialstate.add_transition(targetstate, label, 0.0)


    def __len__(self):
        return len(self.states)

    def __str__(self):
        """Generate an AT&T string representing the FST."""
        # number states arbitrarily based on id()
        ids = [id(s) for s in self.states if s != self.initialstate]
        statenums = {ids[i]:i+1 for i in range(len(ids))}
        statenums[id(self.initialstate)] = 0 # The initial state is always 0
        st = ""
        for s in self.states:
            if len(s.transitions) > 0:
                for label in s.transitions.keys():
                    for transition in s.transitions[label]:
                        st += '{}\t{}\t{}\t{}\n'.format(statenums[id(s)],\
                        statenums[id(transition.targetstate)], '\t'.join(label),\
                        transition.weight)
        for s in self.states:
            if s in self.finalstates:
                st += '{}\t{}\n'.format(statenums[id(s)], s.finalweight)
        return st

    def __and__(self, other):
        return self.intersection(other)

    def __or__(self, other):
        return self.union(other)

    def __sub__(self, other):
        return self.difference(other)

    def __pow__(self, other):
        return self.cross_product(other)

    def __mul__(self, other):
        return self.concatenate(other)

    def __matmul__(self, other):
        return self.compose(other)

    def number_states(self):
        cntr = itertools.count()
        ordered = [self.initialstate] + list(self.states - {self.initialstate})
        return {id(s):(next(cntr) if s.name == None else s.name) for s in ordered}

    def harmonize_alphabet(func):
        @functools.wraps(func)
        def wrapper_decorator(self, other):
            for A, B in [(self, other), (other, self)]:
                if '.' in A.alphabet and (A.alphabet - {'.'}) != (B.alphabet - {'.'}):
                    Aexpand = B.alphabet - A.alphabet - {'.', ''}
                    if A == other:
                        A, _ = other.copy_filtered()
                        other = A # Need to copy to avoid mutating other
                    for s, l, t in list(A.all_transitions(A.states)):
                        if '.' in l:
                            for sym in Aexpand:
                                newl = tuple(lbl if lbl != '.' else sym for lbl in l)
                                s.add_transition(t.targetstate, newl, t.weight)

            newalphabet = self.alphabet | other.alphabet
            value = func(self, other)
            # Do something after
            value.alphabet = newalphabet
            return value
        return wrapper_decorator

    def trim(self):
        """Remove states that aren't both accessible and coaccessible."""
        return self.accessible().coaccessible()

    def accessible(self):
        """Remove states that are not on a path from the initial state."""
        explored = {self.initialstate}
        stack = deque([self.initialstate])
        while stack:
            source = stack.pop()
            for label, transition in source.all_transitions():
                if transition.targetstate not in explored:
                    explored.add(transition.targetstate)
                    stack.append(transition.targetstate)

        self.states = explored
        self.finalstates &= self.states
        return self

    def coaccessible(self):
        """Remove states and transitions to states that have no path to a final state."""
        explored = {self.initialstate}
        stack = deque([self.initialstate])
        inverse = {s:set() for s in self.states} # store all preceding arcs here
        while stack:
            source = stack.pop()
            for target in source.all_targets():
                inverse[target].add(source)
                if target not in explored:
                    explored.add(target)
                    stack.append(target)

        stack = deque([s for s in self.finalstates])
        coaccessible = {s for s in self.finalstates}
        while stack:
            source = stack.pop()
            for previous in inverse[source]:
                if previous not in coaccessible:
                    coaccessible.add(previous)
                    stack.append(previous)

        coaccessible.add(self.initialstate) # Let's make an exception for the initial
        for s in self.states: # Need to also remove transitions to non-coaccessibles
            s.remove_transitions_to_targets(self.states - coaccessible)

        self.states &= coaccessible
        self.finalstates &= self.states
        return self

    def view(self, raw = False, show_weights = False, show_alphabet = True):
        import graphviz
        from IPython.display import display
        def _float_format(num):
            if not show_weights:
                return ""
            s = '{0:.2f}'.format(num).rstrip('0').rstrip('.')
            s = '0' if s == '-0' else s
            return "/" + s

#        g = graphviz.Digraph('FST', filename='fsm.gv')

        sigma = "Σ: {" + ','.join(sorted(a for a in self.alphabet)) + "}" \
            if show_alphabet else ""
        g = graphviz.Digraph('FST', graph_attr={ "label": sigma, "rankdir": "LR" })
        statenums = self.number_states()
        if show_weights == False:
            if any(t.weight != 0.0 for _, _, t in self.all_transitions(self.states)) or \
                  any(s.finalweight != 0.0 for s in self.finalstates):
                  show_weights = True

        g.attr(rankdir='LR', size='8,5')
        g.attr('node', shape='doublecircle', style = 'filled')
        for s in self.finalstates:
            g.node(str(statenums[id(s)]) + _float_format(s.finalweight))

        g.attr('node', shape='circle', style = 'filled')
        for s in self.states:
            if s not in self.finalstates:
                g.node(str(statenums[id(s)]), shape='circle', style = 'filled')
            grouped_targets = defaultdict(set) # {states}
            for label, t in s.all_transitions():
                grouped_targets[t.targetstate] |= {(t.targetstate, label, t.weight)}
            for target, tlabelset in grouped_targets.items():
                if raw == True:
                    labellist = sorted((str(l) + '/' + str(w) for t, l, w in tlabelset))
                else:
                    labellist = sorted((':'.join(label) + _float_format(w) for _, label, w in tlabelset))
                printlabel = ', '.join(labellist)
                if s in self.finalstates:
                    sourcelabel = str(statenums[id(s)]) + _float_format(s.finalweight)
                else:
                    sourcelabel = str(statenums[id(s)])
                if target in self.finalstates:
                    targetlabel = str(statenums[id(target)]) + _float_format(target.finalweight)
                else:
                    targetlabel = str(statenums[id(target)])
                g.edge(sourcelabel, targetlabel, label = printlabel)
        display(graphviz.Source(g))

    def all_transitions(self, states):
        """Enumerate all transitions (state, label, Transition) for an iterable of states."""
        for state in states:
            for label, transitions in state.transitions.items():
                for t in transitions:
                    yield state, label, t

    def scc(self):
        """Calculate the strongly connected components of an FST.

           This is a basic implementation of Tarjan's (1972) algorithm.
           Tarjan, R. E. (1972), "Depth-first search and linear graph algorithms",
           SIAM Journal on Computing, 1 (2): 146–160.

           Returns a set of frozensets of states, one frozenset for each SCC."""

        index = 0
        S = deque([])
        sccs, indices, lowlink, onstack = set(), {}, {}, set()

        def _strongconnect(state):
            nonlocal index, indices, lowlink, onstack, sccs
            indices[state] = index
            lowlink[state] = index
            index += 1
            S.append(state)
            onstack.add(state)
            targets = state.all_targets()
            for target in targets:
                if target not in indices:
                    _strongconnect(target)
                    lowlink[state] = min(lowlink[state], lowlink[target])
                elif target in onstack:
                    lowlink[state] = min(lowlink[state], indices[target])
            if lowlink[state] == indices[state]:
                currscc = set()
                while True:
                    target = S.pop()
                    onstack.remove(target)
                    currscc.add(target)
                    if state == target:
                        break
                sccs.add(frozenset(currscc))

        for s in self.states:
            if s not in indices:
                _strongconnect(s)

        return sccs

    def push_weights(self):
        """Pushes weights toward the initial state. Calls dijkstra and maybe scc."""
        potentials = self.dijkstra_all()
        for s, _, t in self.all_transitions(self.states):
            t.weight += potentials[t.targetstate] - potentials[s]
        for f in self.finalstates:
            f.finalweight = f.finalweight - potentials[f]
        residualweight = potentials[self.initialstate]
        if residualweight != 0.0:
            # Add residual to all exits of initial state SCC and finals in that SCC
            mainscc = next(s for s in self.scc() if self.initialstate in s)
            for s, _, t in self.all_transitions(mainscc):
                if t.targetstate not in mainscc: # We're exiting the main SCC
                    t.weight += residualweight
            for f in mainscc & self.finalstates: # Finals in initial SCC add res w
                f.finalweight += residualweight
        return self

    def copy_mod(self, modlabel = lambda l, w :l, modweight = lambda l, w: w):
        newfst = FST(alphabet = self.alphabet.copy())
        q1q2 = {k:State() for k in self.states}
        newfst.states = set(q1q2.values())
        newfst.finalstates = {q1q2[s] for s in self.finalstates}
        newfst.initialstate = q1q2[self.initialstate]

        for s, lbl, t in self.all_transitions(q1q2.keys()):
            q1q2[s].add_transition(q1q2[t.targetstate], modlabel(lbl, t.weight), modweight(lbl, t.weight))

        for s in self.finalstates:
            q1q2[s].finalweight = s.finalweight

        return newfst

    def copy_filtered(self, statefilter = lambda x: True, labelfilter = lambda x: True):
        newfst = FST(alphabet = self.alphabet.copy())
        q1q2 = {k:State() for k in self.states}
        newfst.states = set(q1q2.values())
        newfst.finalstates = {q1q2[s] for s in self.finalstates}
        newfst.initialstate = q1q2[self.initialstate]

        for s, lbl, t in self.all_transitions(q1q2.keys()):
            if labelfilter(lbl):
                q1q2[s].add_transition(q1q2[t.targetstate], lbl, t.weight)

        for s in self.finalstates:
            q1q2[s].finalweight = s.finalweight

        return newfst, q1q2

    def epsilon_removal(self):
        """Create new epsilon-free FSM equivalent to original."""
        # For each state s, figure out the min-cost w' to hop to a state t with epsilons
        # Then, add the (non-e) transitions of state t to s, adding w' to their cost
        # Also, if t is final and s is not, make s final with cost q.final ⊗ w'
        # If s and t are both final, make s's finalweight s.final ⊕ (q.final ⊗ w')

        eclosures = {s:self.epsilon_closure(s) for s in self.states}
        if all(len(ec) == 0 for ec in eclosures.values()): # bail, no epsilon transitions
            return self
        newfst, mapping = self.copy_filtered(labelfilter = lambda lbl: not all(len(sublabel) == 0 for sublabel in lbl))
        for state, ec in eclosures.items():
            for target, cost in ec.items():
                # copy target's transitions to source
                for label, t in target.all_transitions():
                    if all(len(sublabel) == 0 for sublabel in label): # is epsilon: skip
                        continue
                    mapping[state].add_transition(mapping[t.targetstate], label, cost + t.weight)
                if target in self.finalstates:
                    if state not in self.finalstates:
                        newfst.finalstates.add(mapping[state])
                        mapping[state].finalweight = 0.0
                    mapping[state].finalweight += cost + target.finalweight
        return newfst

    def epsilon_closure(self, state):
        """Find, for a state the set of states reachable by epsilon-hopping."""
        explored, cntr = {}, itertools.count()
        q = [(0.0, next(cntr), state)]
        while q:
            cost, _, source = heapq.heappop(q)
            if source not in explored:
                explored[source] = cost
                for target, weight in source.all_epsilon_targets_cheapest().items():
                    heapq.heappush(q, (cost + weight, next(cntr), target))
        explored.pop(state) # Remove the state where we started from
        return explored

    def dijkstra_all(self):
        return {s:self.dijkstra(s, State.all_targets_cheapest) for s in self.states}

    def dijkstra(self, state, explorermethod):
        """The cost of the cheapest path from state to a final state. Go Edsger!"""
        explored, cntr = {state}, itertools.count()  # decrease-key is for wusses
        q = [(0.0, next(cntr), state)]
        while q:
            w, _ , s = heapq.heappop(q) # Middle is dummy cntr to avoid key ties
            if s == None:       # First None we pull out is the lowest-cost exit
                return w
            explored.add(s)
            if s in self.finalstates:
                # now we push a None state to signal the exit from a final
                heapq.heappush(q, (w + s.finalweight, next(cntr), None))
            for trgt, cost in explorermethod(s).items():
                if trgt not in explored:
                    heapq.heappush(q, (cost + w, next(cntr), trgt))
        return float("inf")

    def words(self):
        """A generator to yield all words. Yay BFS!."""
        Q = deque([(self.initialstate, 0.0, [])])
        while Q:
            s, cost, seq = Q.popleft()
            if s in self.finalstates:
                yield cost + s.finalweight, seq
            for label, t in s.all_transitions():
                Q.append((t.targetstate, cost + t.weight, seq + [label]))

    def words_nbest(self, n):
        return list(itertools.islice(self.words_cheapest(), n))

    def words_cheapest(self):
        """A generator to yield all words in order of cost."""
        cntr = itertools.count()
        Q = [(0.0, next(cntr), self.initialstate, [])]
        while Q:
            cost, _, s, seq = heapq.heappop(Q)
            if s is None:
                yield cost, seq
            else:
                if s in self.finalstates:
                    heapq.heappush(Q, (cost + s.finalweight, next(cntr), None, seq))
                for label, t in s.all_transitions():
                    heapq.heappush(Q, (cost + t.weight, next(cntr), t.targetstate, seq + [label]))

    def determinize_unweighted(self):
        """Determinize with all zero weights."""
        return self.determinize(staterep = lambda s, w: (s, 0.0), oplus = lambda *x: 0.0)

    def determinize_as_dfa(self):
        """Determinize as a DFA with weight moved to label, then apply unweighted det."""
        newfst = self.copy_mod(modlabel = lambda l, w: l + (w,), modweight = lambda l, w: 0.0)
        determinized = newfst.determinize_unweighted() # det, and shift weights back
        return determinized.copy_mod(modlabel = lambda l, _: l[:-1], modweight = lambda l, _: l[-1])

    def determinize(self, staterep = lambda s, w: (s, w), oplus = min):
        """Weighted determinization of FST."""
        newfst = FST(alphabet = self.alphabet.copy())
        firststate = frozenset({staterep(self.initialstate, 0.0)})
        statesets = {firststate:newfst.initialstate}
        if self.initialstate in self.finalstates:
            newfst.finalstates = {newfst.initialstate}
            newfst.initialstate.finalweight = self.initialstate.finalweight

        Q = deque([firststate])
        while Q:
            currentQ = Q.pop()
            collectlabels = {} # temp dict of label:all transitions {(src1, trans1),...}
            for s, _ in currentQ:
                for label, transitions in s.transitions.items():
                    for t in transitions:
                        collectlabels[label] = collectlabels.get(label, set()) | {(s, t)}

            residuals = {s:r for s, r in currentQ}
            for label, tset in collectlabels.items():
                # wprime is the maximum amount the matching outgoing arcs share -
                # some paths may therefore accumulate debt which needs to be passed on
                # and stored in the next state representation for future discharge
                wprime = oplus(t.weight + residuals[s] for s, t in tset)
                # Note the calculation of the weight debt we pass forward, reused w/ finals below
                newQ = frozenset(staterep(t.targetstate, t.weight + residuals[s] - wprime) for s, t in tset)
                if newQ not in statesets:
                    Q.append(newQ)
                    newstate = State()
                    statesets[newQ] = newstate
                    newfst.states.add(statesets[newQ])
                    #statesets[newQ].name = str(newQ)
                else:
                    newstate = statesets[newQ]
                statesets[currentQ].add_transition(newstate, label, wprime)
                if any(t.targetstate in self.finalstates for _, t in tset):
                    newfst.finalstates.add(newstate)
                    # State was final, so we discharge the maximum debt we can
                    newstate.finalweight = oplus(t.targetstate.finalweight + t.weight + \
                        residuals[s] - wprime for s, t in tset if t.targetstate in self.finalstates)
        return newfst

    def minimize(self):
        """Minimize, currently through Brzozowski."""
        return self.reverse().determinize().reverse().determinize()

    def kleene_closure(self, mode = 'star'):
        """T1*. No epsilons here."""
        q1 = {k:State() for k in self.states}
        newfst = FST(alphabet = self.alphabet.copy())

        for lbl, t in self.initialstate.all_transitions():
            newfst.initialstate.add_transition(q1[t.targetstate], lbl, t.weight)

        for s, lbl, t in self.all_transitions(self.states):
            q1[s].add_transition(q1[t.targetstate], lbl, t.weight)

        for s in self.finalstates:
            for lbl, t in self.initialstate.all_transitions():
                q1[s].add_transition(q1[t.targetstate], lbl, t.weight)
            q1[s].finalweight = s.finalweight

        newfst.finalstates = {q1[s] for s in self.finalstates}
        if mode != 'plus' or self.initialstate in self.finalstates:
            newfst.finalstates |= {newfst.initialstate}
            newfst.initialstate.finalweight = 0.0
        newfst.states = set(q1.values()) | {newfst.initialstate}
        return newfst

    def add_weight(self, weight):
        for s in self.finalstates:
            s.finalweight += weight
        return self

    def optional(self):
        """Same as T|0."""
        if self.initialstate in self.finalstates:
            return self
        newinitial = State()

        for lbl, t in self.initialstate.all_transitions():
            newinitial.add_transition(t.targetstate, lbl, t.weight)

        self.initialstate = newinitial
        self.states.add(newinitial)
        self.finalstates.add(newinitial)
        newinitial.finalweight = 0.0
        return self

    @harmonize_alphabet
    def concatenate(self, other):
        """Concatenation of T1T2. No epsilons. May produce non-accessible states."""
        ocopy, _ = other.copy_filtered() # Need to copy since self may equal other
        q1q2 = {k:State() for k in self.states | ocopy.states}

        for s, lbl, t in self.all_transitions(q1q2.keys()):
            q1q2[s].add_transition(q1q2[t.targetstate], lbl, t.weight)
        for s in self.finalstates:
            for lbl2, t2 in ocopy.initialstate.all_transitions():
                q1q2[s].add_transition(q1q2[t2.targetstate], lbl2, t2.weight + s.finalweight)

        newfst = FST()
        newfst.initialstate = q1q2[self.initialstate]
        newfst.finalstates = {q1q2[f] for f in ocopy.finalstates}
        for s in ocopy.finalstates:
            q1q2[s].finalweight = s.finalweight
        if ocopy.initialstate in ocopy.finalstates:
            newfst.finalstates |= {q1q2[f] for f in self.finalstates}
            for f in self.finalstates:
                q1q2[f].finalweight = f.finalweight + ocopy.initialstate.finalweight
        newfst.states = set(q1q2.values())
        return newfst

    @harmonize_alphabet
    def cross_product(self, other):
        """Perform the cross-product of T1, T2 through composition."""
        newfst_a =  self.copy_mod(modlabel = lambda l, _: l + ('',))
        newfst_b = other.copy_mod(modlabel = lambda l, _: ('',) + l)
        return newfst_a.compose(newfst_b)

    @harmonize_alphabet
    def compose(self, other):
        """Composition of A,B; will expand an acceptor into 2-tape FST on-the-fly."""

        def _mergetuples(x, y):
            if len(x) == 1:
                return x + y[1:]
            elif len(y) == 1:
                return x[:-1] + y
            return x[:-1] + y[1:]

        # Mode 0: allow A=x:0 B=0:y (>0), A=x:y B=y:z (>0), A=x:0 B=wait (>1) A=wait 0:y (>2)
        # Mode 1: x:0 B=wait (>1), x:y y:z (>0)
        # Mode 2: A=wait 0:y (>2), x:y y:z (>0)

        newfst = FST()
        Q = deque([(self.initialstate, other.initialstate, 0)])
        S = {(self.initialstate, other.initialstate, 0): newfst.initialstate}
        while Q:
            A, B, mode = Q.pop()
            currentstate = S[(A, B, mode)]
            if A in self.finalstates and B in other.finalstates:
                newfst.finalstates.add(currentstate)
                currentstate.finalweight = A.finalweight + B.finalweight # TODO: oplus
            for matchsym in A.transitionsout.keys():
                if mode == 0 or matchsym != '': # A=x:y B=y:z, or x:0 0:y (only in mode 0)
                    for outtrans in A.transitionsout.get(matchsym, ()):
                        for intrans in B.transitionsin.get(matchsym, ()):
                            target1 = outtrans[1].targetstate # Transition
                            target2 = intrans[1].targetstate  # Transition
                            if (target1, target2, 0) not in S:
                                Q.append((target1, target2, 0))
                                S[(target1, target2, 0)] = State()
                                newfst.states.add(S[(target1, target2, 0)])
                            # Keep intermediate
                            # currentstate.add_transition(S[(target1, target2)], outtrans[1].label[:-1] + intrans[1].label, outtrans[1].weight + intrans[1].weight)
                            newlabel = _mergetuples(outtrans[1].label, intrans[1].label)
                            currentstate.add_transition(S[(target1, target2, 0)], newlabel, outtrans[1].weight + intrans[1].weight)
            for outtrans in A.transitionsout.get('', ()): # B waits
                if mode == 2:
                    break
                target1, target2 = outtrans[1].targetstate, B
                if (target1, target2, 1) not in S:
                    Q.append((target1, target2, 1))
                    S[(target1, target2, 1)] = State()
                    newfst.states.add(S[(target1, target2, 1)])
                newlabel = outtrans[1].label
                currentstate.add_transition(S[(target1, target2, 1)], newlabel, outtrans[1].weight)
            for intrans in B.transitionsin.get('', ()): # A waits
                if mode == 1:
                    break
                target1, target2 = A, intrans[1].targetstate
                if (target1, target2, 2) not in S:
                    Q.append((target1, target2, 2))
                    S[(target1, target2, 2)] = State()
                    newfst.states.add(S[(target1, target2, 2)])
                newlabel = intrans[1].label
                currentstate.add_transition(S[(target1, target2, 2)], newlabel, intrans[1].weight)
        return newfst

    def invert(self):
        """Calculates the inverse of a transducer, i.e. flips label tuples around."""
        for s in self.states:
            s.transitions  = {lbl[::-1]:tr for lbl, tr in s.transitions.items()}
        return self

    def ignore(self, other):
        """A, ignoring intevening instances of B."""
        #  A @ $^proj-1(.|'':B)
        return self.compose(FST(label = ('.',)).union(FST(label = ('',)).\
               cross_product(other)).kleene_closure()).project(-1)

    def project(self, dim):
        """Let's project. dim = -1 will get output proj regardless of # of tapes."""
        for s in self.states:
            s.transitions  = {lbl[dim]:tr for lbl, tr in s.transitions.items()}
        return self

    def reverse(self):
        """Reversal of FST, epsilon-free."""
        newfst = FST(alphabet = self.alphabet.copy())
        newfst.initialstate = State()
        mapping = {k:State() for k in self.states}
        newfst.states = set(mapping.values()) | {newfst.initialstate}
        newfst.finalstates = {mapping[self.initialstate]}
        if self.initialstate in self.finalstates:
            newfst.finalstates.add(newfst.initialstate)
            newfst.initialstate.finalweight = self.initialstate.finalweight
        mapping[self.initialstate].finalweight = 0.0

        for s, lbl, t in self.all_transitions(self.states):
            mapping[t.targetstate].add_transition(mapping[s], lbl, t.weight)
            if t.targetstate in self.finalstates:
                newfst.initialstate.add_transition(mapping[s], lbl, t.weight + \
                t.targetstate.finalweight)
        return newfst

    @harmonize_alphabet
    def union(self, other):
        q1, q2 = {k:State() for k in self.states}, {k:State() for k in other.states}
        newfst = FST()
        newfst.states = set(q1.values()) | set(q2.values()) | {newfst.initialstate}

        for lbl, t in self.initialstate.all_transitions():
            newfst.initialstate.add_transition(q1[t.targetstate], lbl, t.weight)

        for lbl, t in other.initialstate.all_transitions():
            newfst.initialstate.add_transition(q2[t.targetstate], lbl, t.weight)

        for s, lbl, t in self.all_transitions(self.states):
            q1[s].add_transition(q1[t.targetstate], lbl, t.weight)

        for s, lbl, t in other.all_transitions(other.states):
            q2[s].add_transition(q2[t.targetstate], lbl, t.weight)

        for s in self.finalstates:
            newfst.finalstates.add(q1[s])
            q1[s].finalweight = s.finalweight

        for s in other.finalstates:
            newfst.finalstates.add(q2[s])
            q2[s].finalweight = s.finalweight

        newfst.finalstates = {q1[s] for s in self.finalstates} | {q2[s] for s in other.finalstates}
        if self.initialstate in self.finalstates or other.initialstate in other.finalstates:
            newfst.finalstates.add(newfst.initialstate)
            newfst.initialstate.finalweight = min(self.initialstate.finalweight, \
                                                  other.initialstate.finalweight)

        return newfst

    @harmonize_alphabet
    def intersection(self, other):
        return self.product(other, finalf = all, oplus = operator.add, pathfollow = lambda x,y: x & y)

    @harmonize_alphabet
    def difference(self, other):
        return self.product(other, finalf = lambda x: x[0] and not x[1], oplus = lambda x,y: x)

    def product(self, other, finalf = any, oplus = min, pathfollow = lambda x,y: x|y):
        newfst = FST()
        Q = deque([(self.initialstate, other.initialstate)])
        S = {(self.initialstate, other.initialstate): newfst.initialstate}
        dead1, dead2 = State(finalweight = float("inf")), State(finalweight = float("inf"))
        while Q:
            t1s, t2s = Q.pop()
            currentstate = S[(t1s, t2s)]
            if finalf((t1s in self.finalstates, t2s in other.finalstates)):
                newfst.finalstates.add(currentstate)
                currentstate.finalweight = oplus(t1s.finalweight, t2s.finalweight)
            # Get all outgoing labels we want to follow
            for lbl in pathfollow(t1s.transitions.keys(), t2s.transitions.keys()):
                for outtr in t1s.transitions.get(lbl, (Transition(dead1, lbl, float('inf')), )):
                    for intr in t2s.transitions.get(lbl, (Transition(dead2, lbl, float('inf')), )):
                        if (outtr.targetstate, intr.targetstate) not in S:
                            Q.append((outtr.targetstate, intr.targetstate))
                            S[(outtr.targetstate, intr.targetstate)] = State()
                            newfst.states.add(S[(outtr.targetstate, intr.targetstate)])
                        currentstate.add_transition(S[(outtr.targetstate, intr.targetstate)], lbl, oplus(outtr.weight, intr.weight))
        return newfst


class Transition:
    __slots__ = ['targetstate', 'label', 'weight']
    def __init__(self, targetstate, label, weight):
        self.targetstate = targetstate
        self.label = label
        self.weight = weight


class State:
    def __init__(self, finalweight = None, name = None):
        __slots__ = ['transitions', '_transitionsin', '_transitionsout', 'finalweight', 'name']
        # Index both the first and last elements lazily (e.g. compose needs it)
        self.transitions = dict()     # (l_1,...,l_n):{transition1, transition2, ...}
        self._transitionsin = None    # l_1:(label, transition1), (label, transition2), ... }
        self._transitionsout = None   # l_n:(label, transition1), (label, transition2, ...)}
        if finalweight is None:
            finalweight = float("inf")
        self.finalweight = finalweight
        self.name = name

    @property
    def transitionsin(self):
        if self._transitionsin is None:
            self._transitionsin = defaultdict(set)
            for label, newtrans in self.transitions.items():
                for t in newtrans:
                    self._transitionsin[label[0]] |= {(label, t)}
        return self._transitionsin

    @property
    def transitionsout(self):
        if self._transitionsout is None:
            self._transitionsout = defaultdict(set)
            for label, newtrans in self.transitions.items():
                for t in newtrans:
                    self._transitionsout[label[-1]] |= {(label, t)}
        return self._transitionsout

    def remove_transitions_to_targets(self, targets):
        """Remove all transitions from self to any state in the set targets."""
        newt = {}
        for label, transitions in self.transitions.items():
            newt[label] = {t for t in transitions if t.targetstate not in targets}
            if len(newt[label]) == 0:
                newt.pop(label)
        self.transitions = newt

    def add_transition(self, other, label, weight):
        """Add transition from self to other with label and weight."""
        newtrans = Transition(other, label, weight)
        self.transitions[label] = self.transitions.get(label, set()) | {newtrans}

    def all_transitions(self):
        """Generator for all transitions out from a given state."""
        for label, transitions in self.transitions.items():
            for t in transitions:
                yield label, t

    def all_targets(self):
        """Returns the set of states a state has transitions to."""
        return {t.targetstate for tr in self.transitions.values() for t in tr}

    def all_epsilon_targets_cheapest(self):
        """Returns a dict of states a state transitions to (cheapest) with epsilon."""
        targets = defaultdict(lambda: float("inf"))
        for lbl, tr in self.transitions.items():
            if all(len(sublabel) == 0 for sublabel in lbl): # funky epsilon-check
                for s in tr:
                    targets[s.targetstate] = min(targets[s.targetstate], s.weight)
        return targets

    def all_targets_cheapest(self):
        """Returns a dict of states a state transitions to (cheapest)."""
        targets = defaultdict(lambda: float("inf"))
        for tr in self.transitions.values():
            for s in tr:
                targets[s.targetstate] = min(targets[s.targetstate], s.weight)
        return targets