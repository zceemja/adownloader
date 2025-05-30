# Python Async Downloader

A tiny and fast python async parallel file downloader script using [aiohttp](https://docs.aiohttp.org/en/stable/index.html) 
and [rich](https://rich.readthedocs.io/en/stable/introduction.html) with resuming support.

Demo of resuming downloads from .m8u file:
![demo gif](https://github.com/user-attachments/assets/f4e6960e-aa1b-449e-980c-507bd3a60b31)

## Usage
Using [uv](https://docs.astral.sh/uv/) (recommended):
```shell
uvx https://github.com/zceemja/adownloader.git url1 url2 url3 ...
```

Building with `pip`
```shell
pip install -U git+https://github.com/zceemja/adownloader.git
python -m adownloader url1 url2 url3 ...
```

## Why?
There are many alternatives, some of the popular ones I found:
- [parfive](https://github.com/Cadair/parfive) - great and you probably should use it instead, does not show total download
- [aiodl](https://github.com/cshuaimin/aiodl) - downloads a single file using multiple connections
- [aiodownload](https://github.com/jelloslinger/aiodownload) - no cli
- [multithread](https://github.com/DashLt/multithread) - great, but archived and no out-of-the-box cli
- [pyDownload](https://github.com/parth-verma/pyDownload) - does not use asyncio

As far as I understand, none of these use libraries supports download continuation - 
resuming download when restarting the program. 
As I am unfortunate enough to have a slow unstable internet, I wrote my own solution.
