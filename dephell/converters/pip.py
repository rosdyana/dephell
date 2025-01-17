# built-in
from itertools import chain
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

# external
from dephell_discover import Root as PackageRoot
from dephell_links import DirLink
from pip._internal.download import PipSession
from pip._internal.index import PackageFinder
from pip._internal.req import parse_requirements

# app
from ..context_tools import chdir
from ..controllers import DependencyMaker, RepositoriesRegistry
from ..models import RootDependency
from ..repositories import WarehouseBaseRepo, WarehouseLocalRepo
from .base import BaseConverter


class PIPConverter(BaseConverter):
    sep = ' \\\n  '

    def can_parse(self, path: Path, content: Optional[str] = None) -> bool:
        if isinstance(path, str):
            path = Path(path)

        if path.name == 'requirements.txt':
            if path.with_name('requirements.in').exists():
                return (self.lock is True)
            if path.with_name('requirements.lock').exists():
                return (self.lock is False)
            return True

        if self.lock:
            return (path.name == 'requirements.lock')
        else:
            return (path.name == 'requirements.in')

    def load(self, path) -> RootDependency:
        if isinstance(path, str):
            path = Path(path)
        path = self._make_source_path_absolute(path)
        self._resolve_path = path.parent

        root = RootDependency(
            package=PackageRoot(path=self.project_path or path.parent),
        )

        finder = self._get_finder()

        # https://github.com/pypa/pip/blob/master/src/pip/_internal/req/constructors.py
        with chdir(self.resolve_path or path.parent):
            reqs = parse_requirements(
                filename=str(path),
                session=PipSession(),
                finder=finder,
            )

            deps = []
            for req in reqs:
                # https://github.com/pypa/pip/blob/master/src/pip/_internal/req/req_install.py
                if req.req is None:
                    req.req = SimpleNamespace(
                        name=req.link.url.split('/')[-1],
                        specifier='*',
                        marker=None,
                        extras=None,
                    )
                deps.extend(DependencyMaker.from_requirement(
                    source=root,
                    req=req.req,
                    url=req.link and req.link.url,
                    editable=req.editable,
                ))

        # update repository
        if finder.index_urls or finder.find_links:
            repo = RepositoriesRegistry()
            for url in chain(finder.index_urls, finder.find_links):
                repo.add_repo(url=url)
            repo.attach_config()
            for dep in deps:
                if isinstance(dep.repo, WarehouseBaseRepo):
                    dep.repo = repo

        root.attach_dependencies(deps)
        return root

    def dumps(self, reqs, project: Optional[RootDependency] = None,
              content: Optional[str] = None) -> str:
        lines = []

        # get repos urls
        urls = []
        paths = []
        names = set()
        for req in reqs:
            if not isinstance(req.dep.repo, WarehouseBaseRepo):
                continue
            for repo in req.dep.repo.repos:
                if repo.from_config:
                    continue
                if repo.name in names:
                    continue
                names.add(repo.name)
                if isinstance(repo, WarehouseLocalRepo):
                    paths.append(repo.path)
                else:
                    urls.append(repo.pretty_url)
        # dump repos urls
        if urls:
            lines.append('-i ' + urls[0])
        for url in urls[1:]:
            lines.append('--extra-index-url ' + url)
        for path in paths:
            lines.append('--find-links ' + path)

        # disable hashes when dir-based deps are presented
        # https://github.com/dephell/dephell/issues/41
        with_hashes = not any(isinstance(req.dep.link, DirLink) for req in reqs)

        for req in reqs:
            lines.append(self._format_req(req=req, with_hashes=with_hashes))
        return '\n'.join(lines) + '\n'

    @staticmethod
    def _get_finder():
        try:
            return PackageFinder(find_links=[], index_urls=[], session=PipSession())
        except TypeError:
            pass

        from pip._internal.models.search_scope import SearchScope
        from pip._internal.models.selection_prefs import SelectionPreferences

        return PackageFinder.create(
            search_scope=SearchScope(find_links=[], index_urls=[]),
            selection_prefs=SelectionPreferences(allow_yanked=False),
            session=PipSession(),
        )

    # https://github.com/pypa/packaging/blob/master/packaging/requirements.py
    # https://github.com/jazzband/pip-tools/blob/master/piptools/utils.py
    def _format_req(self, req, *, with_hashes: bool = True) -> str:
        line = ''
        if req.editable:
            line += '-e '
        if req.link is not None:
            link = req.link.long
            path = Path(link.split('#egg=')[0])
            if path.exists():
                link = str(self._make_dependency_path_relative(path))
                link = link.replace('\\', '/')
                if '/' not in link:
                    link = './' + link
            line += link
        else:
            line += req.raw_name
        if req.extras:
            line += '[{extras}]'.format(extras=','.join(req.extras))
        if req.version:
            line += req.version
        if req.markers:
            line += '; ' + req.markers
        if with_hashes and req.hashes:
            for digest in req.hashes:
                # https://github.com/jazzband/pip-tools/blob/master/piptools/writer.py
                line += '{sep}--hash {hash}'.format(
                    sep=self.sep,
                    hash=digest,
                )
        if self.lock and req.sources:
            line += '{sep}# ^ from {sources}'.format(
                sep=self.sep,
                sources=', '.join(req.sources),
            )
        return line
