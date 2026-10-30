### RQ3: scale at which the C4 interval lies entirely above 1 (GPU faster than the best CPU engine side)

| Contrast | Suite | First scale CI > 1 | Stable from | First point estimate > 1 | Verdict per scale |
|---|---|---|---|---|---|
| C4 | baseline | SF30 | SF30 | SF30 | sf1:cpu faster; sf10:cpu faster; sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 | complex | SF30 | SF30 | SF30 | sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 (both GPU engines) | baseline | SF30 | SF30 | SF30 | sf1:cpu faster; sf10:cpu faster; sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |
| C4 (both GPU engines) | complex | SF30 | SF30 | SF30 | sf30:gpu faster; sf100:gpu faster; sf300:gpu faster |

gpu faster: CI entirely above 1; cpu faster: entirely below 1; inconclusive: interval contains 1.
