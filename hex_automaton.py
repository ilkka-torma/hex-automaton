import math
from fractions import Fraction as fr
import time
import multiprocessing as mp
from enum import Enum
import sys
import pickle
import argparse
import fractions

NUM_THREADS = 2
CHUNK_SIZE = 200

class CompMode(Enum):
    SQUARE_CYCLE = 0 # don't recompute, use O(n^2) space, return cycle
    LINSQRT_CYCLE = 1 # recompute twice, use O(n^{3/2}) space, return cycle
    LINEAR_NOCYCLE = 2 # recompute once, use O(n) space, return only density and length

COMP_MODE = None

PRINT_NFA = False
PRINT_CYCLE = True


def forbs_with_highest(p):
    "All forbidden sets with p as lex-highest point"
    x, y = p
    s = -1 if (x+y)%2 else 1
    # neighborhood
    yield [(x-2,y),(x-1,y),(x,y),(x-1,y-s)]
    # confused horizontal neighbors
    yield [(x-3,y),(x-2,y+s),(x-1,y-s),(x,y)]
    # confused vertical neighbors
    if not (x+y)%2:
        yield [(x-2,y-1),(x-2,y),(x,y-1),(x,y)]
    # confused distance-1 at angle
    yield [(x,y),(x-1,y),(x-2,y),(x-2,y-s),(x-3,y-s),(x-2,y-2*s)]
    yield [(x,y),(x-1,y),(x-1,y-s),(x-1,y+s),(x-2,y+s),(x-3,y+s)]
    # confused straight distance-1
    yield [(x,y),(x-1,y),(x-1,y-s),(x-3,y),(x-3,y-s),(x-4,y)]

def pats(domain):
    if not domain:
        yield dict()
    else:
        vec = domain.pop()
        for pat in pats(domain):
            pat2 = pat.copy()
            pat[vec] = 0
            yield pat
            pat2[vec] = 1
            yield pat2
        domain.add(vec)

def wrap(height, shear, p):
    x,y = p
    return (x+(y//height)*shear, y%height)

class HexNFA:
    # Alphabet is weights
    # Frontier has size 2 x height, and is slanted
    # States are collections of forbidden sets that intersect frontier and its right side
    # They are stored as integers to save space
    # Shear must be positive and odd, height must be positive
    # trans has type dict[state] -> (dict[state] -> weight) and only stores minimum weights

    def __init__(self, height, shear, rotate=False, sym_bound=None, verbose=False, immediately_relabel=True):
        forb_bound = 3*shear + 10
        if sym_bound is not None and height%2:
            raise Exception("height must be even if symmetry is enforced")
        if height%2 != shear%2:
            raise Exception("height and shear must be equal mod 2")
        if verbose:
            print("constructing hexagon NFA with height", height, "shear", shear, "no symmetry" if sym_bound is None else "symmetry %s"%sym_bound, "rotated" if rotate else "not rotated")
        self.height = height
        self.shear = shear
        self.frontier = set()
        self.border_forbs = []
        for y in range(self.height):
            x = self.border_at(y)
            self.frontier.add((x,y))
            self.frontier.add((x+1,y))
            for i in range(forb_bound):
                for forb in forbs_with_highest((x+i,y)):
                    # keep those forbidden patterns that have a chance to be handled on the next step
                    if any(a <= self.border_at(b)+1 for (a,b) in forb) and\
                       all(a >= self.border_at(b) for (a,b) in forb):
                        self.border_forbs.append(tuple(set(wrap(height, shear, p) for p in forb)))
        self.states = set([0])
        self.trans = dict()
        if verbose:
            print("done with #forbs", len(self.border_forbs), "#frontier", len(self.frontier))
        self.immediately_relabel = immediately_relabel
        self.sym_bound = sym_bound
        self.rotate = rotate

    def populate(self, verbose=False, report=5000):
        self.s2idict = {}
        self.running = 0
        def state_to_idx(s):
            if not self.immediately_relabel:
                return s
            
            if s in self.s2idict:
                return self.s2idict[s]
            else:
                self.s2idict[s] = self.running
                self.running += 1
                return self.running-1
        
        # populate states and transitions
        if verbose:
            print("populating hexagon NFA")
        n = 0
        task_q = mp.Queue()
        res_q = mp.Queue()
        processes = [mp.Process(target=populate_worker,
                                args=(self.height, self.shear, self.border_forbs, self.frontier, self.sym_bound, self.rotate,
                                      task_q, res_q))
                     for _ in range(NUM_THREADS)]
        for pr in processes:
            pr.start()
        undone = len(self.states)
        for state in self.states:
            task_q.put([state])

        assert len(self.states) == 1 # the above for loop is over singleton

        qq = []
        while undone:
            res = res_q.get()
            if type(res) == int: 
                undone -= res
                continue
            for (state, front_or_weight, new_state) in res:
                if new_state not in self.states:
                    self.states.add(new_state)
                    if verbose and len(self.states)%report == 0:
                        print("states", len(self.states), "to process", undone)
                    qq.append(new_state)
                    if len(qq) >= CHUNK_SIZE:
                        task_q.put(qq)
                        undone += len(qq)
                        qq = []
                        
                    
                state_idx = state_to_idx(state)
                new_state_idx = state_to_idx(new_state)
                if state_idx not in self.trans:
                    self.trans[state_idx] = dict()
                try:
                    self.trans[state_idx][new_state_idx] = min(self.trans[state_idx][new_state_idx], front_or_weight)
                except KeyError:
                    self.trans[state_idx][new_state_idx] = front_or_weight
            if qq != []:
                task_q.put(qq)
                undone += len(qq)
                qq = []
        for pr in processes:
            pr.terminate()
        print("done with #states", len(self.states))

    def relabel(self):
        if self.immediately_relabel:
            return
        else:
            st = list(sorted(self.states))
            ts = {s : i for (i,s) in enumerate(st)}
            self.states = set(range(len(st)))
            self.trans = {ts[p] : {ts[q] : w for (q,w) in qs.items()}
                          for (p,qs) in self.trans.items()}

    def square_min_density_cycle(self, bound_len=None, verbose=False, report=50):
        "Assume states are relabeled to range(len(states))"
        if verbose:
            print("finding min density cycle in O(n^2) space")
        n = len(self.states)
        if bound_len is None:
            m = n
        else:
            m = min(n, bound_len)
        # split transdict among processes; they can do the search backwards
        # each modifies only its own part of mins so we can share it
        # initialize with 2*height*n, which is theoretical max val
        # access like mins[n*k+q]
        global mins, opt_prevs
        max_w = 2*self.height*m
        mins = mp.Array('i', [0 if q==k==0 else max_w
                              for k in range(m+1)
                              for q in range(n)],
                        lock=False)
        opt_prevs = mp.Array('i', [-1
                                   for k in range(m+1)
                                   for q in range(n)],
                             lock=False)
        task_qs = [((i*n)//NUM_THREADS, ((i+1)*n)//NUM_THREADS,  mp.Queue())
                   for i in range(NUM_THREADS)]
        res_q = mp.Queue()
        procs = [mp.Process(target=square_min_worker,
                            args=(mins, opt_prevs, n, m, max_w,
                                  {p : qs for (p,qs) in self.trans.items() if a <= p < b},
                                  task_q, res_q))
                 for (a, b, task_q) in task_qs]
        for proc in procs:
            proc.start()
        for k in range(1, m+1):
            if verbose and k%report==0:
                print("round", k, "/", n)
            for (_, _, task_q) in task_qs:
                task_q.put(k)
            for _ in range(NUM_THREADS):
                res = res_q.get()
                assert res is None
        for (_, _, task_q) in task_qs:
            task_q.put(None)
        
        min_num = math.inf
        min_val = None
        reachermost = None
        for _ in range(NUM_THREADS):
            num, val, reacher = res_q.get()
            if num < min_num or (num == min_num and (min_val == None or min_val < val)):
                min_num = num
                min_val = val
                reachermost = reacher
        for proc in procs:
            proc.terminate()

        #min_val *= 2
            
        path = [reachermost]
        cur = reachermost
        for i in range(m, 0, -1):
            nxt = opt_prevs[n*i+cur]
            path.append(nxt)
            cur = nxt
        
        # check path length and weight
        assert len(path) == m+1
        assert sum(self.trans[path[k]][path[k+1]] for k in range(m)) == mins[n*m+path[0]]
        
        for cycle_len in range(1, m):
            for i in range(m - cycle_len):
                if path[i] == path[i+cycle_len]:
                    summe = 0
                    for k in range(cycle_len):
                        summe += self.trans[path[i+k]][path[i+k+1]]
                    if summe/cycle_len == min_num:
                        min_cycle = list(path[i:i+cycle_len])
                        break
                    else:
                        print(summe/cycle_len, min_num)
            else:
                continue
            break
        
        return min_num, len(min_cycle), min_cycle, self.get_cycle_labels(min_cycle)

    def linsqrt_min_density_cycle(self, bound_len=None, verbose=False, report=50):
        "Assume states are relabeled to range(len(states))"
        if verbose:
            print("finding min density cycle on O(n^(2/3)) space")
        n = len(self.states)
        if bound_len is None:
            m = n
        else:
            m = min(n, bound_len)
        # split transdict among processes; they can do the search backwards
        # each modifies only its own part of mins so we can share it
        # initialize with 2*height*n, which is theoretical max val
        # access like sparse/dense_mins[n*k+q] and opt_prevs[n*k+q]
        # phases:
        # 1. compute sparse mins using dense mins
        #    in particular, last row is d_q(n) for each state q
        # 2. compute max of (d_q(n)-d_q(k))/(n-k) of each state q using dense mins
        #    take minimum over q, also get length of cycle
        # 3. for minimizing q, iteratively compute path segments between two rows of dense mins
        #    this needs dense mins and opt prevs
        #    stitch together into a single path, find cycle on it
        global dense_mins, sparse_mins, opt_prevs
        max_w = 2*self.height*m
        sqrtm = int(math.ceil(m**0.5))+1
        # dense_mins represents rows int(i*sqrt(n)) + j for 0 <= j <= ceil(sqrt(n)) for varying i
        dense_mins = mp.Array('i', [0 if k==q==0 else max_w
                                    for k in range(sqrtm)
                                    for q in range(n)],
                        lock=False)
        # sparse_mins represents rows int(i*sqrt(n)) for 0 <= i <= ceil(sqrt(n))
        sparse_rows = [max(0, min(n, (n*k)//sqrtm)) for k in range(sqrtm+1)]
        print("using rows", sparse_rows)
        sparse_mins = mp.Array('i', [max_w
                                     for _ in sparse_rows
                                     for q in range(n)],
                        lock=False)
        opt_prevs = mp.Array('i', [-1
                                   for k in range(sqrtm)
                                   for q in range(n)],
                             lock=False)
        task_qs = [((i*n)//NUM_THREADS, ((i+1)*n)//NUM_THREADS,  mp.Queue())
                   for i in range(NUM_THREADS)]
        res_q = mp.Queue()
        procs = [mp.Process(target=linsqrt_min_worker,
                            args=(dense_mins, sparse_mins, opt_prevs, n, m, max_w, sparse_rows,
                                  {p : qs for (p,qs) in self.trans.items() if a <= p < b},
                                  task_q, res_q))
                 for (a, b, task_q) in task_qs]
        for proc in procs:
            proc.start()

        # phase 1: populate dense mins
        for k in range(0,m+1):
            if verbose and k%report==0:
                print("phase 1 round", k, "/", m)
            for (_, _, task_q) in task_qs:
                task_q.put(k)
            for _ in range(NUM_THREADS):
                res = res_q.get()
                assert res is None

        # phase 2: compute minimum q
        for p in self.trans:
            dense_mins[p] = 0 if p==0 else max_w
            dense_mins[n+p] = max_w
        min_things = math.inf, 0, 0
        for k in range(1, m):
            if verbose and k%report==0:
                print("phase 2 round", k, "/", m)
            for (_, _, task_q) in task_qs:
                task_q.put(k)
            for _ in range(NUM_THREADS):
                res = res_q.get()
                assert res is None
        for (_, _, task_q) in task_qs:
            task_q.put(None)
            res = res_q.get()
            min_things = min(min_things, res)
        min_d, min_len, min_q = min_things
        print("min density", min_d, "min len", min_len)

        # phase 3: compute path from q
        path = [min_q]
        cur = min_q
        rnd = 1
        for (lo, hi) in reversed(list(zip(sparse_rows, sparse_rows[1:]))):
            for k in range(lo, hi+1):
                if verbose and rnd%report==0:
                    print("phase 3 round", rnd, "/", m+len(sparse_rows)-2)
                rnd += 1
                for (_, _, task_q) in task_qs:
                    task_q.put((lo,k))
                for _ in range(NUM_THREADS):
                    res = res_q.get()
                    assert res is None
            for i in reversed(range(lo+1, hi+1)):
                nxt = opt_prevs[n*(i-lo)+cur]
                path.append(nxt)
                cur = nxt
        
        for proc in procs:
            proc.terminate()
            
        # check path length and weight
        assert len(path) == m+1
        assert sum(self.trans[path[k]][path[k+1]] for k in range(m)) == sparse_mins[n*(len(sparse_rows)-1)+path[0]]

        #print(path, min_len)
        for cycle_len in range(1, m):
            for i in range(m - cycle_len):
                if path[i] == path[i+cycle_len]:
                    summe = 0
                    for k in range(cycle_len):
                        summe += self.trans[path[i+k]][path[i+k+1]]
                    if summe/cycle_len == min_d:
                        min_cycle = list(path[i:i+cycle_len])
                        break
            else:
                continue
            break
            
        return min_d, len(min_cycle), min_cycle, self.get_cycle_labels(min_cycle)

    def linear_min_density_cycle(self, bound_len=None, verbose=False, report=50):
        "Assume states are relabeled to range(len(states))"
        if verbose:
            print("finding min density of cycle in O(n) space")
        n = len(self.states)
        if bound_len is None:
            m = n
        else:
            m = min(n, bound_len)
        # split transdict among processes; they can do the search backwards
        # each modifies only its own part of mins so we can share it
        # access like mins[n*a+q] for a in [0,1,2]
        # initialize with 2*height*n, which is theoretical max val
        # 0 and 1 are "workspace" arrays, 2 is where we store values for n
        global mins
        max_w = 2*self.height*n
        mins = mp.Array('i', [0 if q==k==0 else max_w
                              for k in range(3)
                              for q in range(n)],
                        lock=False)
        task_qs = [((i*n)//NUM_THREADS, ((i+1)*n)//NUM_THREADS,  mp.Queue())
                   for i in range(NUM_THREADS)]
        res_q = mp.Queue()
        procs = [mp.Process(target=linear_min_worker,
                            args=(mins, n, m, max_w,
                                  {p : qs for (p,qs) in self.trans.items() if a <= p < b},
                                  task_q, res_q))
                 for (a,b,task_q) in task_qs]
        for proc in procs:
            proc.start()
        for p in [1,2]:
            for k in range(1, (m+1) if p == 1 else m):
                if verbose and k%report==0:
                    print("phase", p, "round", k, "/", m if p == 1 else (m-1))
                for (_, _, task_q) in task_qs:
                    task_q.put(k)
                for _ in range(NUM_THREADS):
                    res = res_q.get()
                    assert res is None
            if p == 1:
                for st in range(n):
                    mins[st] = 0 if st == 0 else max_w
        for (_, _, task_q) in task_qs:
            task_q.put(None)
        min_num = math.inf
        min_val = None
        min_state = 0
        for _ in range(NUM_THREADS):
            num, val, state, maxes = res_q.get()
            if num < min_num or (num == min_num and (min_val == None or min_val < val)):
                min_num = num
                min_val = val
                min_state = state
        for proc in procs:
            proc.terminate()
        return min_num, min_val, min_state


    def get_cycle_labels(self, cycle_as_states, verbose=False):
        #return cycle_as_states
        numf = len(self.border_forbs)
        border_sets = [set(forb) for forb in self.border_forbs]
        self.compute_i2sdict()
        labels = []
        for s in range(len(cycle_as_states)):
            ass = cycle_as_states[s]
            bss = cycle_as_states[(s+1)%len(cycle_as_states)]
            a = self.i2sdict[ass]
            b = self.i2sdict[bss]
            if verbose: print("from", a, "to", b)
            shifted = [(f,0) for f in self.border_forbs]
            i = 0
            n = a
            while n:
                if n%2:
                    ix = i%numf
                    tr = i//numf
                    shifted.append((self.border_forbs[ix], tr+2))
                n = n//2
                i += 1
            frontier = set(self.frontier)
            for new_front in pats(frontier):
                try:
                    if sum(new_front.values()) != self.trans[ass][bss]:
                        continue
                except KeyError:
                    print(ass, self.trans[ass], bss)
                    1/0
                if verbose: print("trying out", new_front)
                new_pairs = set()
                sym_pairs = dict()
                for pair in shifted:
                    forb, tr = pair
                    over = False
                    for (x,y) in forb:
                        if x-tr >= border_at(self.height, self.shear, y)+2:
                            over = True
                        if new_front.get((x-tr+(y//self.height)*self.shear, y%self.height), 0) == 1:
                            # this forb can be discarded
                            break
                    else:
                        # forb was not discarded
                        if over:
                            # forb can still be handled later
                            new_pairs.add(pair)
                        else:
                            # forb can't be handled, reject state
                            break
                else:
                    # choose minimal state along rotations and reflections
                    if rotate:
                        min_state = math.inf
                        for rot in range(self.height//2):
                            for ref in [True, False]:
                                new_state = 0
                                for (forb, tr) in new_pairs:
                                    ix = border_sets.index(set((x, (y+2*rot if ref else 1-(y+2*rot))%self.height) for (x,y) in forb))
                                    sym_pairs[ix%(numf//2), tr] = 1 - sym_pairs.get((ix%(numf//2), tr), 0)
                                    
                                    new_state += 2**(numf*tr + ix)
                                min_state = min(min_state, new_state)
                        new_state = min_state
                    else:
                        new_state = 0
                        for (forb, tr) in new_pairs:
                            ix = self.border_forbs.index(forb)
                            sym_pairs[ix%(numf//2), tr] = 1 - sym_pairs.get((ix%(numf//2), tr), 0)
                            
                            new_state += 2**(numf*tr + ix)
                    if new_state == b:
                        labels.append(new_front)
                        break
                        
        return labels


    def compute_i2sdict(self):
        self.i2sdict = {}
        for k in self.s2idict:
            self.i2sdict[self.s2idict[k]] = k
        
    def border_at(self, y):
        return (-y*self.shear) // self.height
        
    def accepts(self, w_path, repetitions=True):
        cur = init = set([0])
        r = 1
        while True:
            for (i, w) in enumerate(w_path):
                nexts = set(st for cst in cur for (st, tr_w) in self.trans[cst].items() if tr_w <= w)
                if nexts:
                    cur = nexts
                else:
                    return (False, (cur, w, i, r, [self.trans[cst] for cst in cur]))
            if (not repetitions) or cur == init:
                break
            r += 1
            init = cur
        return (True, r)

def border_at(height, shear, y):
    return (-y*shear) // height

def populate_worker(height, shear, border_forbs, frontier, sym_bound, rotate, task_queue, res_queue):
    numf = len(border_forbs)
    border_sets = [set(forb) for forb in border_forbs]
    while True:
        states = task_queue.get()
        ret = []
        for state in states:
            # state is a number encoding a set of shifted forbs
            shifted = [(f,0) for f in border_forbs]
            i = 0
            n = state
            while n:
                if n%2:
                    ix = i%numf
                    tr = i//numf
                    shifted.append((border_forbs[ix], tr+2))
                n = n//2
                i += 1
            for new_front in pats(frontier):
                new_pairs = set()
                sym_pairs = dict()
                for pair in shifted:
                    forb, tr = pair
                    over = False
                    for (x,y) in forb:
                        if x-tr >= border_at(height, shear, y)+2:
                            over = True
                        if new_front.get((x-tr+(y//height)*shear, y%height), 0) == 1:
                            # this forb can be discarded
                            break
                    else:
                        # forb was not discarded
                        if over:
                            # forb can still be handled later
                            new_pairs.add(pair)
                        else:
                            # forb can't be handled, reject state
                            break
                else:
                    # choose minimal state along rotations and reflections
                    if rotate:
                        min_state = math.inf
                        for rot in range(height//2):
                            for ref in [True, False]:
                                new_state = 0
                                for (forb, tr) in new_pairs:
                                    ix = border_sets.index(set((x, (y+2*rot if ref else 1-(y+2*rot))%height) for (x,y) in forb))
                                    sym_pairs[ix%(numf//2), tr] = 1 - sym_pairs.get((ix%(numf//2), tr), 0)
                                    
                                    new_state += 2**(numf*tr + ix)
                                min_state = min(min_state, new_state)
                        new_state = min_state
                    else:
                        new_state = 0
                        for (forb, tr) in new_pairs:
                            ix = border_forbs.index(forb)
                            sym_pairs[ix%(numf//2), tr] = 1 - sym_pairs.get((ix%(numf//2), tr), 0)
                            
                            new_state += 2**(numf*tr + ix)
                    if sym_bound is None or sum(sym_pairs.values()) <= sym_bound:
                        
                        ret.append((state, sum(new_front.values()), new_state))
                        if len(ret) >= CHUNK_SIZE:
                            res_queue.put(ret)
                            ret = []
                    
        if ret != []:
            res_queue.put(ret)
        res_queue.put(len(states))


def square_min_worker(the_mins, the_opt_prevs, n, m, max_w, trans, task_q, res_q):
    # share array
    global mins, opt_prevs
    mins = the_mins
    opt_prevs = the_opt_prevs
    # fill part of the distance array one layer at a time
    while True:
        k = task_q.get()
        for (p, qs) in trans.items():
            new_min, opt_prev = min((mins[n*(k-1)+q]+w, q) for (q, w) in qs.items())
            mins[n*k+p] = new_min
            opt_prevs[n*k+p] = opt_prev
        res_q.put(None)
        if k == n:
            break
    # compute minimum for assigned states
    dummy = task_q.get()
    assert dummy is None
    the_min = math.inf
    min_val = None
    min_state = 0
    for p in trans:
        the_max = 0
        max_val = None
        for k in range(1,n):
            num = (mins[m*n+p]-mins[k*n+p])/(n-k)
            if (num > the_max) or (num == the_max and m-k < max_val):
                the_max = num
                max_val = n-k
        if (the_max < the_min) or (the_max == the_min and max_val < min_val):
            the_min = the_max
            min_val = max_val
            min_state = p
    res_q.put((the_min, min_val, min_state))

def linsqrt_min_worker(the_dense_mins, the_sparse_mins, the_opt_prevs, n, m, max_w, sparse_rows, trans, task_q, res_q):
    # share arrays
    global dense_mins, sparse_mins, opt_prevs
    dense_mins = the_dense_mins
    sparse_mins = the_sparse_mins
    opt_prevs = the_opt_prevs
    
    # compute sparse distance array
    # expect to receive k,j, where k=0,1,...,n; send None after each
    while True:
        k = task_q.get()
        if k is None:
            break
        cur = n*(k%2)
        pre = n*((k-1)%2)
        if k > 0:
            for (p, qs) in trans.items():
                new_min = min(dense_mins[pre+q]+w for (q,w) in qs.items())
                dense_mins[cur+p] = min(max_w, new_min)
        try:
            i = sparse_rows.index(k)
            for p in trans:
                sparse_mins[n*i+p] = dense_mins[cur+p]
        except ValueError:
            pass
        res_q.put(None)
        if k == n:
            break
    
    # recompute previous layers, simultaneously compute minimum for assigned states
    # expect to receive 1, ..., n, send None after each
    # finally receive None
    maxes = {p : (-1, math.inf) for p in trans}
    while True:
        k = task_q.get()
        if k is None:
            break
        cur = n*(k%2)
        pre = n*((k-1)%2)
        for (p, qs) in trans.items():
            new_min = min(dense_mins[pre+q]+w for (q,w) in qs.items())
            dense_mins[cur+p] = min(max_w, new_min)
            the_max, max_val = maxes[p]
            num = (sparse_mins[n*(len(sparse_rows)-1)+p]-dense_mins[cur+p])/(m-k)
            if (num > the_max) or (num == the_max and m-k < max_val):
                maxes[p] = (num, m-k)
        res_q.put(None)
        
    the_min = math.inf
    min_val = math.inf
    min_state = 0
    for (p, (the_max, max_val)) in maxes.items():
        if (the_max < the_min) or (the_max == the_min and max_val < min_val):
            the_min = the_max
            min_val = max_val
            min_state = p
    res_q.put((the_min, min_val, min_state))

    # recompute each gap in reverse order, also computing optimal predecessors
    # expect to receive tuples (k, k==low), send None after each
    # finally receive None
    
    while True:
        task = task_q.get()
        if task is None:
            break
        lo, k = task
        if lo == k:
            i = sparse_rows.index(k)
            for q in trans:
                dense_mins[q] = sparse_mins[n*i+q]
        else:
            k2 = k-lo
            for (p, qs) in trans.items():
                min_w, min_q = min((dense_mins[n*(k2-1)+q]+w, q) for (q, w) in qs.items())
                dense_mins[n*k2+p] = min_w
                opt_prevs[n*k2+p] = min_q
        res_q.put(None)

def linear_min_worker(the_mins, n, m, max_w, trans, task_q, res_q):
    # share array
    global mins
    mins = the_mins
    # compute the last layer of distance array
    # expect to receive 1, ..., n, send None after each
    while True:
        k = task_q.get()
        if k < m:
            cur = n*(k%2)
            pre = n*((k-1)%2)
        elif k == m:
            cur = n*2
            pre = n*((k-1)%2)
        for (p, qs) in trans.items():
            new_min = min(mins[pre+q]+w for (q,w) in qs.items())
            mins[cur+p] = min(max_w, new_min)
        res_q.put(None)
        if k == m:
            break
    # recompute previous layers, simultaneously compute minimum for assigned states
    # expect to receive 1, ..., n-1, send None after each, then receive None and send result
    maxes = {p : (-1, math.inf) for p in trans}
    while True:
        k = task_q.get()
        if k is None:
            break
        cur = n*(k%2)
        pre = n*((k-1)%2)
        for (p, qs) in trans.items():
            new_min = min(mins[pre+q]+w for (q,w) in qs.items())
            mins[cur+p] = min(max_w, new_min)
            the_max, max_val = maxes[p]
            num = (mins[2*n+p]-mins[cur+p])/(m-k)
            if (num > the_max) or (num == the_max and m-k < max_val):
                maxes[p] = (num, m-k)
        res_q.put(None)
    the_min = math.inf
    min_val = math.inf
    min_state = 0
    for (p, (the_max, max_val)) in maxes.items():
        if (the_max < the_min) or (the_max == the_min and max_val < min_val):
            the_min = the_max
            min_val = max_val
            min_state = p
    res_q.put((the_min, min_val, min_state, maxes))


def prints(x,y):
    print(x, y)
    return(y)

def kek(f):
    return str(f)+"~"+("%.3f"%float(f))
    
if __name__ == "__main__":
    starttime = time.time()
    
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("height", metavar='h', type=int)
    arg_parser.add_argument("shear", metavar='s', type=int)
    arg_parser.add_argument("mode", metavar='m', type=str, choices=['L','S','Q'])
    arg_parser.add_argument("--symbreak", '-S', type=int, required=False)
    arg_parser.add_argument("--karpbound", '-K', type=int, required=False)
    arg_parser.add_argument("--rotate", '-R', action="store_true", required=False)
    arg_parser.add_argument("--infile", '-i', type=str, required=False)
    arg_parser.add_argument("--reportpop", '-r1', type=int, required=False, default=5000)
    arg_parser.add_argument("--reportcyc", '-r2', type=int, required=False, default=50)
    arg_parser.add_argument("--threads", '-t', type=int, required=False, default=1)
    arg_parser.add_argument("--chunksize", '-c', type=int, required=False, default=200)
    args = arg_parser.parse_args()
    
    h = args.height
    s = args.shear
    if (h+s)%2:
        print("height+shear must be even")
        quit()
    bound_len = args.karpbound
    sym_b = args.symbreak
    rotate = args.rotate
    infile = args.infile
    reportcyc = args.reportcyc
    reportpop = args.reportpop
    if sym_b is not None and (h%2 or (h//2+s//2)%2):
        print("for symmetry breaking, height/2+shear/2 must be even")
        quit()
    if rotate and (h%2 or s):
        print("for rotation symmetry, height must be even and shear must be 0")
        quit()
    if sym_b is not None and rotate:
        print("warning: symmetry breaking may be incompatible with rotation")
    if args.mode == "L":
        COMP_MODE = CompMode.LINEAR_NOCYCLE
    elif args.mode == "S":
        COMP_MODE = CompMode.LINSQRT_CYCLE
    elif args.mode == "Q":
        COMP_MODE = CompMode.SQUARE_CYCLE
    if rotate and COMP_MODE != CompMode.LINEAR_NOCYCLE:
        print("warning: rotation produces a cycle with each label independently rotated/reflected")
    NUM_THREADS = args.threads
    CHUNK_SIZE = args.chunksize
    print("threads", NUM_THREADS, "chunk size", CHUNK_SIZE)
    print("using height %s shear %s mode %s symmetry-breaking %s Karp bound %s rotation symmetry %s" % (h, s, COMP_MODE, sym_b, bound_len, rotate))
    
    if infile is None:
        nfa = HexNFA(h,s,sym_bound=sym_b,verbose=True,immediately_relabel=True,rotate=rotate)
        nfa.populate(verbose=True, report=reportpop)
        print("time taken after pop:", time.time()-starttime, "seconds")
        nfa.relabel()
        if PRINT_NFA:
            print(nfa.trans)
        savename = "hex-aut-%s-%s-%s-%s.pickle"%(h,s,sym_b,rotate)
        with open(savename, 'wb') as f:
            print("saving automaton to", savename)
            pickle.dump(nfa, f)
    else:
        print("loading automaton from", infile)
        with open(infile, 'rb') as f:
            nfa = pickle.load(f)
    
    if COMP_MODE == CompMode.SQUARE_CYCLE:
        dens, minlen, stcyc, cyc = nfa.square_min_density_cycle(bound_len=bound_len, verbose=True, report=reportcyc)
    elif COMP_MODE == CompMode.LINSQRT_CYCLE:        
        dens, minlen, stcyc, cyc = nfa.linsqrt_min_density_cycle(bound_len=bound_len, verbose=True, report=reportcyc)
    elif COMP_MODE == CompMode.LINEAR_NOCYCLE:
        dens, minlen, minst = nfa.linear_min_density_cycle(bound_len=bound_len, verbose=True, report=reportcyc)
    print("height %s, shear %s, bound %s, symmetry %s, rotation %s completed" % (h, s, bound_len, sym_b, rotate))
    if bound_len is not None and len(nfa.states) <= bound_len:
        print("bound was not needed")
    if COMP_MODE == CompMode.LINEAR_NOCYCLE:
        print("density", dens/(2*h), "known bounds", 23/55, 53/126)
    else:
        print("density", fractions.Fraction(sum(b for fr in cyc for b in fr.values()), 2*h*len(cyc)), "~", dens/(2*h), "known bounds", 23/55, 53/126)
    print("cycle length", minlen, "concretely", minlen*2)
    if COMP_MODE != CompMode.LINEAR_NOCYCLE and PRINT_CYCLE:
        print("cycle:")
        print(cyc)
        cyc_w = [sum(x.values()) for x in cyc]
        # sanity check: cycle is accepted by nfa
        res, reason = nfa.accepts(cyc_w, repetitions=True)
        if res:
            print("cycle^n accepted for all n,", reason, "was enough")
        else:
            st, front, ix, n, tr = reason
            print("cycle ^", n, "not accepted due to state", st, "with label", front, "at position", ix)
            print("available transitions:", tr)
            print("(this is bad)")
    print("total time taken:", time.time()-starttime, "seconds")
