# Copyright: 2015 Tim Harder <radhermit@gmail.com
# License: BSD/GPL2

"""Display keywords for specified targets."""

import argparse

from snakeoil.lists import stable_unique

from pkgcore.util import commandline, parserestrict
from pkgcore.repository.util import RepositoryGroup


class StoreTarget(argparse._AppendAction):

    def __call__(self, parser, namespace, values, option_string=None):
        items = []
        try:
            for x in values:
                items.append((x, parserestrict.parse_match(x)))
        except parserestrict.ParseError as e:
            raise argparse.ArgumentError(self, "atom is malformed: '%s'" % x)
        setattr(namespace, self.dest, items)


argparser = commandline.mk_argparser(description=__doc__)

argparser.add_argument(
    "--no-filters", action='store_true', default=False,
    help="disable all license filtering, visibility filtering "
         "(ACCEPT_KEYWORDS, package masking, etc)")
argparser.add_argument(
    'targets', metavar='target',  nargs='+', action=StoreTarget,
    help="extended atom matching of packages")
argparser.add_argument(
    '-r', '--repo',
    action=commandline.StoreRepoObject, priority=29,
    help='repo(s) to use (default from domain if omitted)')


@argparser.bind_delayed_default(30, 'repos')
def setup_repos(namespace, attr):
    # Get repo(s) to operate on.
    if namespace.repo:
        repos = [namespace.repo]
    else:
        repos = namespace.domain.source_repos

    setattr(namespace, attr, repos)


@argparser.bind_main_func
def main(options, out, err):
    for token, restriction in options.targets:
        pkgs = []
        for repo in options.repos:
            pkgs += repo.match(restriction)

        if not pkgs:
            err.write("no matches for '%s'" % (token,))
            continue

        for pkg in pkgs:
            out.write('%s: %s' % (pkg.cpvstr, ', '.join(pkg.keywords)))
