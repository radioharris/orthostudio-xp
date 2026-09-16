# Behaviour specifications

Written **before** the code of any rule ported from Ortho4XP or invented here.

Each spec states: the rule in plain language, the inputs and outputs, the origin in Ortho4XP
(file:line) when ported, the decision (keep / fix / drop) with the reason, the acceptance test
(byte-identical, numeric tolerance, or semantic comparison), and known "wanted differences"
from Ortho4XP.

Since decision 0010 no test compares OrthoStudio XP with Ortho4XP. The acceptance sections record
what was measured against Ortho4XP when a rule was ported; the oracle tests, frozen references and
`tools/oracle` scripts they name are gone. The tests that remain check OrthoStudio XP on its own,
some of them against a reference implementation written into the test.
