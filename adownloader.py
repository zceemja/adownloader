import asyncio
from pathlib import Path
from typing import AsyncIterator

import aiohttp
import aiofiles
import rich
import rich.progress
import rich.theme
from collections import namedtuple

_FileInfo = namedtuple('FileInfo', ['url', 'path', 'size', 'accept_ranges'])


class SortedRichProgress(rich.progress.Progress):
    """ Makes so the newest tasks are at the top """
    def get_renderables(self):
        table = self.make_tasks_table(list(sorted(self.tasks, key=lambda task: task.id, reverse=True)))
        yield table

class AsyncDownloader:
    CHUNK_SIZE = 65536

    def __init__(self, download_dir:str|Path='.', concurrent_downloads: int=5,
                 allow_override=True, allow_partial=True, timeout=30, console=None):
        self.console = console
        if self.console is None:
            self.console = rich.get_console()
            self.console.use_theme(rich.theme.Theme({"progress.description": "yellow"}))
        self.semaphore = asyncio.Semaphore(concurrent_downloads)
        self.progress = SortedRichProgress(
            rich.progress.TextColumn("[progress.description]{task.description}"),
            rich.progress.TaskProgressColumn(),
            rich.progress.BarColumn(bar_width=None),
            rich.progress.DownloadColumn(binary_units=True),
            rich.progress.TransferSpeedColumn(),
            rich.progress.TimeRemainingColumn(),
            console=console, expand=True,
        )
        self.session: aiohttp.ClientSession|None = None
        self.download_dir = Path(download_dir).absolute()
        self.allow_override = allow_override
        self.allow_partial = allow_partial
        self.timeout = timeout
        self.max_retires = 3
        self.default_headers = {}

        self._main_task: rich.progress.TaskID = 0
        self._file_queue: asyncio.Queue[Path] = asyncio.Queue()
        self._download_tasks: list[asyncio.Task] = []
        self._files_checked = 0
        self._prog_lock = asyncio.Lock()

    async def _check_file(self, request:str|tuple[str, str]|tuple[str,Path]):
        filename = None
        if isinstance(request, tuple):
            url, filename = request
        else:
            url = request
        async with self.semaphore:
            async with self._prog_lock:  # do this first before exception
                checked = self.progress.tasks[self._main_task].fields['files_checked'] + 1
                total_files = self.progress.tasks[self._main_task].fields['files_total']
                desc = "Total" if checked == total_files else f"Checking {checked}/{total_files}"
                self.progress.update(self._main_task, description=desc, files_checked=checked)
            async with self.session.head(url, allow_redirects=True, timeout=self.timeout) as resp:
                if not (resp.status <= 400):
                    self.console.print(f'Status [red]{resp.status}[/red]: {url}')
                    resp.raise_for_status()
                try:
                    size = int(resp.headers.get('content-length', 0))
                except ValueError:
                    size = 0
                if filename is None:
                    if resp.content_disposition is not None:
                        filename = resp.content_disposition.filename
                    else:
                        filename = resp.url.path.split('/')[-1]
            async with self._prog_lock:
                total = self.progress.tasks[self._main_task].total or 0
                self.progress.update(self._main_task, total=total + size)
            if isinstance(filename, str):
                filename = self.download_dir / filename
            return _FileInfo(resp.url, filename, size, resp.headers.get('accept-ranges'))

    async def _download_file(self, file: _FileInfo | None, retry=0) -> tuple[_FileInfo, int, bool]:
        if file.path.exists():
            if file.path.stat().st_size == file.size:
                self.progress.update(self._main_task, advance=file.size)
                return file, retry, True
            elif self.allow_override:
                self.console.print(f'[red]File already exists, but has different size, overriding[/red] [progress.description]{file.path}[/progress.description]')
            else:
                self.console.print(f'[red]File already exists, but has different size, skipping[/red] [progress.description]{file.path}[/progress.description]')
                return file.path, retry, True
        use_partial = self.allow_partial and file.accept_ranges == 'bytes'  # Don't use if not supported by the server
        if retry > 0:
            # 1 retry 4 seconds, 2 = 7 sec, 6 ~= 17 sec and goes towards 60 sec max
            await asyncio.sleep(retry * 60 / (retry + 15))
        async with self.semaphore:
            # Always download to .part in case the download is interrupted one could tell the difference
            filepath_part = file.path.with_suffix('.part')
            headers = self.default_headers.copy()
            offset = 0
            if use_partial and filepath_part.exists():
                offset = filepath_part.stat().st_size
                headers['Range'] = f'bytes={offset}-'
            try:
                async with self.session.get(file.url, headers=headers, timeout=self.timeout) as resp:
                    resp.raise_for_status()
                    task_suffix = '' if retry == 0 else f' (retry {retry})'
                    task = self.progress.add_task(file.path.name + task_suffix, total=file.size, completed=offset)
                    self.progress.update(self._main_task, advance=offset)
                    async with aiofiles.open(filepath_part, mode='ab' if offset > 0 else 'wb') as f:
                        async for data in resp.content.iter_chunked(self.CHUNK_SIZE):
                            await f.write(data)
                            self.progress.update(task, advance=len(data))
                            self.progress.update(self._main_task, advance=len(data))
            except TimeoutError:
                self.console.print(f'[bold red]Timeout[bold red] [progress.description]{file.path.name}')
                self.progress.remove_task(task)
                return file, retry, False
            filepath_part.rename(file.path)
            self.console.print(f'Downloaded [progress.description]{file.path.name}')
            self.progress.remove_task(task)
            return file, retry, True

    async def download_generator(self, *urls:str|tuple[str, str]|tuple[str,Path]) -> AsyncIterator[Path|None]:
        self.console.print(f'Downloading {len(urls)} files to [yellow]{self.download_dir}')
        if self.session is None:
            self.session = aiohttp.ClientSession()
        with self.progress:
            self._main_task = self.progress.add_task(f"Checking 0/{len(urls)}", total=None, files_checked=0, files_total=len(urls))
            check_tasks = [asyncio.create_task(self._check_file(url)) for url in urls]
            dl_tasks = []
            for fileinfo in asyncio.as_completed(check_tasks):
                dl_tasks.append(asyncio.create_task(self._download_file(await fileinfo)))
            for dl_task in dl_tasks:
                    fileinfo, retry, ok = await dl_task
                    if retry >= self.max_retires:
                        raise StopAsyncIteration
                    elif ok:
                        yield fileinfo.path
                    else:
                        dl_tasks.append(asyncio.create_task(self._download_file(fileinfo, retry + 1)))


    async def download(self, *urls:str|tuple[str, str]|tuple[str,Path]):
        return [file async for file in self.download_generator(*urls)]

def download_files(*urls:str|tuple[str, str]|tuple[str,Path], **kwargs):
    return asyncio.run(AsyncDownloader(**kwargs).download(*urls))


if __name__ == "__main__":
    import argparse
    from rich_argparse import RichHelpFormatter

    parser = argparse.ArgumentParser("adownloader-rich", formatter_class=RichHelpFormatter, description=
    """Download multiple files concurrently, with rich progress bar and continuation support.""")
    parser.add_argument("urls", nargs="+", help="URLs to download")
    parser.add_argument("-o", "--download-dir", default='.', help="Directory to download files to")
    parser.add_argument("-n", "--concurrent-downloads", type=int, default=5, help="Number of concurrent downloads")
    parser.add_argument("-O", "--no-override", action="store_true", help="Allow overriding existing files that don't match size")
    parser.add_argument("-P", "--no-partial", action="store_true", help="Don't use partial downloads")
    parser.add_argument("-t", "--timeout", type=float, default=30, help="Connection timeout in seconds")
    args = parser.parse_args()

    download_files(
        *args.urls, download_dir=args.download_dir, concurrent_downloads=args.concurrent_downloads,
        allow_override=not args.no_override, allow_partial=not args.no_partial, timeout=args.timeout
    )
