# Hex automaton
A python script for computing minimum densities of periodic identifying codes on the infinite hexagonal grid.

## Usage

The script takes at least a height `h` (which must be positive) and a shear `s` (which must be nonnegative and have the same parity as the height).
It computes the minimum density of an identifying code on the infinite hexagonal grid with periods `(h,s)` and `(x,0)` over all integers `x > 0`.

Required positional arguments:
- `h` is the y-component of the period vector.
- `s` is the x-component of the period vector.
- `m` is the mode of computation. It can be either `Q` (for quadratic space), `L` (for linear space) or `S` (for `n^(3/2)` space). Modes `Q` and `S` compute an explicit cycle, mode `L` does not.

Optional arguments affecting the search:
- `-S` can only be used if `h`, `s` and `h/2+s/2` are all even. It "breaks symmetry" by restricting to state sets whose top half and bottom half differ by at most the given number of forbidden sets. Thus, `h=2k -S m` is somewhere between `h=k` and `h=2k`, both in terms of time/memory requirements and the set of codes that are searched through.
- `-K` restricts Karp's algorithm by using a fixed (smaller) value in place of `n`, the number of states. We give no guarantees that the result is indicative of anything.
- `-R` can only be used if `h` is even and `s` is 0. It prunes the state space by rotating and/or reflecting the state set to a lexicographically minimal version after each transition.

Optional technical arguments:
- `-i` reads the automaton from a file instead of computing it. If `-i` is not given, the automaton is computed and saved to a file.
- `-r1` controls how often the populating function reports its progress.
- `-r2` controls how often the cycle search function reports its progress.
- `-t` sets the number of threads for the populating and search functions.
- `-c` sets the size of each chunk sent to the populating threads.
