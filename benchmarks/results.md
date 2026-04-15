| Command | Mean [ms] | Min [ms] | Max [ms] | Relative |
|:---|---:|---:|---:|---:|
| `small: zlib, 1 platform` | 620.1 ± 37.7 | 581.2 | 656.4 | 1.00 |
| `small: zlib, 3 platforms` | 819.3 ± 56.9 | 760.2 | 873.7 | 1.32 ± 0.12 |
| `medium: py+scipy+etc, 1 platform` | 3916.9 ± 22.3 | 3892.1 | 3935.2 | 6.32 ± 0.39 |
| `medium: py+scipy+etc, 3 platforms` | 4185.9 ± 24.0 | 4169.2 | 4213.4 | 6.75 ± 0.41 |
| `large: py+torch+etc, 1 platform` | 226661.5 ± 67515.0 | 162476.2 | 297074.6 | 365.50 ± 111.11 |
| `large: py+torch+etc, 3 platforms` | 194259.6 ± 16304.9 | 175899.7 | 207050.0 | 313.25 ± 32.46 |
