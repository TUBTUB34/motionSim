# App_DoughFactory independent source audit

Audited the saved program from `tests/full-backup.tar` without running URScript or connecting to a robot. `expected-values.json` records the source SHA-256, per-command arithmetic, configuration values and geometry checks. Its scalar arithmetic was calculated separately from the application's expression resolver.

## Expected source results

- 1,920 source lines; 27 `movej`, 34 `movel`, 9 `optimovej`, 1 `optimovel`: 71 motion occurrences and 35 positive blend radii. These are source occurrences, not execution counts.
- 8 payload calls and 2 TCP calls.
- URP version 5.24.0 references `../default`, so the matching installation is `/programs/default.installation`.
- Installation and first script settings agree: 3.6 kg, CoG `[0.001, 0, 0.132]` m; TCP `[0, 0, 0.247, 0, 3.1414, 0]`. Later program constants specify empty/full payloads of 4.7/6.3 kg, CoG `[0, -0.01, 0.1]` m, and TCP rotation `d2r(179.99)`. These differences must be shown as configuration changes, not hidden or assumed physically incorrect.

The source defines `v_scale=4` and `a_scale=2.5` once. With those definitions, the three linear speed tiers are 2, 5 and 10 m/s; acceleration tiers are 1.25, 3.125 and 6.25 m/s². The three joint speed tiers are 240, 400 and 560 deg/s; acceleration tiers are 150, 250 and 350 deg/s². These are configured source expressions, not measured motion, and definition-based estimates do not prove the constants were initialized on every runtime path.

There are 17 standard TCP acceleration expressions above the application's 2.5 m/s² CB/e-Series starting guideline: script lines 360, 414, 448, 616, 623, 637, 644, 737, 741, 744, 797, 807, 826, 828, 843, 1747 and 1835. No standard MoveJ acceleration expression exceeds the application's 800 deg/s² starting guideline. OptiMove fractions require separate treatment. No generic velocity specification is inferred.

Several MoveL calls use variables named `vr`/`ar`; their numeric values must still be interpreted in MoveL's metre-based parameter units. For example, lines 797/807/826/828 configure velocity `4*d2r(100)=6.98131700798` m/s and acceleration `2.5*d2r(100)=4.36332312999` m/s².

## Reference PDF discrepancies

The supplied reference uses a useful A4 report format: summary, checklist, category findings and footer. It has 10 pages. Its numerical results are not a reliable oracle:

- It counts motion calls quoted inside PolyScope `$` label lines as commands, giving 38 MoveJ/68 MoveL and 14 payload calls instead of the actual counts above.
- Repeated zero-distance blend claims pair a label with its own executable command, for example lines 598/599.
- Lines 737 and 741 are mutually exclusive `if`/`else` alternatives; they must not be compared as consecutive moves. Their target separation is not a traveled segment.
- The reference flags only the literal `a=3.0` at line 448, missing other expressions above the starting guideline.

Match the reference's presentation, while correcting these numerical errors and retaining clear source-review limitations.

## Geometry oracle and limits

Lines 826 and 828 target `transition_pose` and `pose_trans(transition_pose, p[0,-0.021,0.042,0,0,0])`. The source-target separation is `sqrt(0.021²+0.042²) = 0.0469574275275` m, because rigid rotation preserves the displacement norm. Their combined blend radius is 0.005 m, below that separation. This calculation does not establish the exact blended trajectory length.

Runtime PLC offsets, contact detection, inverse kinematics, moving threads, branches and repetitions prevent a verified total executed distance. Unknown geometry must remain unknown rather than zero. Configured speeds and accelerations cannot establish actual robot performance or compliance with active safety-mode limits.
