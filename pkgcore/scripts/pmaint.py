# Copyright: 2005-2011 Brian Harring <ferringb@gmail.com>: GPL/BSD2
# Copyright: 2006 Marien Zwart <marienz@gentoo.org>
# License: BSD/GPL2

"""
repository maintainence
"""

__all__ = ("sync", "sync_main", "copy", "copy_main", "regen", "regen_main",
    "perl_rebuild", "perl_rebuild_main", "env_update", "env_update_main")

from pkgcore.util import commandline
from snakeoil.demandload import demandload
demandload(globals(),
    'os',
    'errno',
    'threading:Event',
    'threading:Thread',
    'Queue',
    'time:time,sleep',
    'snakeoil.osutils:pjoin,listdir_dirs',
    'pkgcore:spawn',
    'pkgcore.repository:multiplex',
    'pkgcore.package:mutated',
    'pkgcore.fs:contents,livefs',
    'pkgcore.ebuild:atom,errors,digest,processor,triggers',
    'pkgcore.merge:triggers@merge_triggers',
    'pkgcore.restrictions.boolean:OrRestriction',
    'pkgcore.sync:base@sync_base',
    'snakeoil.compatibility:any',
    're',
)

def format_seq(seq, formatter=repr):
    if not seq:
        seq = None
    elif len(seq) == 1:
        seq = seq[0]
    else:
        seq = tuple(sorted(str(x) for x in seq))
    return formatter(seq)


shared_options = (commandline.mk_argparser(domain=False, add_help=False),)
argparse_parser = commandline.mk_argparser(suppress=True, parents=shared_options)
subparsers = argparse_parser.add_subparsers(description="general system maintenance")


# inconsistant argparse POS; needed due to nargs='*', it tries supplying the
# default as if it was given via the commandline
class ReposStoreConfig(commandline.StoreConfigObject):

    def _real_call(self, parser, namespace, values, option_string=None):
        if values == []:
            values = namespace.config.repo.keys()
        return commandline.StoreConfigObject._real_call(self, parser, namespace,
            values, option_string=option_string)


sync = subparsers.add_parser("sync", parents=shared_options,
    help="synchronize a local repository with it's defined remote")
sync.add_argument('repos', nargs='*', help="repositories to sync",
    action=ReposStoreConfig, store_name=True,
    config_type='repo')
@sync.bind_main_func
def sync_main(options, out, err):
    """Update a local repositories to match their remote parent"""
    config = options.config
    succeeded, failed = [], []
    seen = set()
    for name, repo in options.repos:
        if repo in seen:
            out.write("*** skipping %r, already synced" % name)
            continue
        seen.add(repo)
        ops = repo.operations
        if not ops.supports("sync"):
            continue
        out.write("*** syncing %r..." % name)
        try:
            ret = ops.sync()
        except sync_base.syncer_exception, se:
            out.write("*** failed syncing %r- caught exception %r" % (name, se))
            failed.append(name)
            continue
        if not ret:
            out.write("*** failed syncing %r" % name)
            failed.append(name)
        else:
            succeeded.append(name)
            out.write("*** synced %r" % name)
    total = len(succeeded) + len(failed)
    if total > 1:
        if succeeded:
            out.write("*** synced %s" % format_seq(sorted(succeeded)))
        if failed:
            err.write("!!! failed sync'ing %s" % format_seq(sorted(failed)))
    if failed:
        return 1
    return 0


copy = subparsers.add_parser("copy", parents=shared_options,
    help="copy binpkgs between repositories; primarily useful for "
    "quickpkging a livefs pkg")
copy.add_argument('target_repo', action=commandline.StoreConfigObject,
    config_type='repo', writable=True,
    help="repository to add packages to")
copy.add_argument('--source-repo', '-s', default=None,
    action=commandline.StoreConfigObject, config_type='repo',
    help="copy strictly from the supplied repository; else it copies from "
    "wherever a match is found")
commandline.make_query(copy, nargs='+', dest='query',
    help="packages matching any of these restrictions will be selected "
    "for copying")
copy.add_argument('-i', '--ignore-existing', default=False, action='store_true',
    help="if a matching pkg already exists in the target, don't update it")

@copy.bind_main_func
def copy_main(options, out, err):
    """Copy pkgs between repositories."""

    src_repo = options.source_repo
    if src_repo is None:
        src_repo = multiplex.tree(*options.config.repo.values())
    trg_repo = options.target_repo
    src_repo = options.source_repo

    failures = False

    for pkg in src_repo.itermatch(options.query):
        if options.ignore_existing and trg_repo.has_match(pkg.versioned_atom):
            out.write("skipping %s; it exists already." % (pkg,))
            continue

        out.write("copying %s... " % (pkg,))
        if getattr(getattr(pkg, 'repo', None), 'livefs', False):
            out.write("forcing regen of contents due to src being livefs..")
            new_contents = contents.contentsSet(mutable=True)
            for fsobj in pkg.contents:
                try:
                    new_contents.add(livefs.gen_obj(fsobj.location))
                except OSError, oe:
                    if oe.errno != errno.ENOENT:
                        err.write("failed accessing fs obj %r; %r\n"
                            "aborting this copy" %
                            (fsobj, oe))
                        failures = True
                        new_contents = None
                        break
                    err.write("warning: dropping fs obj %r since it "
                        "doesn't exist" % fsobj)
            if new_contents is None:
                continue
            pkg = mutated.MutatedPkg(pkg, {'contents':new_contents})

        trg_repo.operations.install_or_replace(pkg).finish()

        out.write("completed\n")
    if failures:
        return 1
    return 0


def regen_iter(iterable, err):
    for x in iterable:
        try:
            x.keywords
        except RuntimeError:
            raise
        except Exception, e:
            err.write("caught exception %s for %s" % (e, x))

def reclaim_threads(threads, err):
    for x in threads:
        try:
            x.join()
        except RuntimeError:
            raise
        except Exception, e:
            err.write("caught exception %s reclaiming thread" % (e,))

def _get_default_jobs(namespace, attr):
    # we intentionally overschedule for SMP; the main python thread
    # isn't too busy, thus we want to keep all bash workers going.
    val = spawn.get_proc_count()
    if val > 1:
        val += 1
    setattr(namespace, attr, val)

regen = subparsers.add_parser("regen", parents=shared_options,
    help="regenerate repository caches")
regen.add_argument("--disable-eclass-preloading", action='store_true',
    default=False,
    help="For regen operation, pkgcore internally turns on an "
    "optimization that preloads eclasses into individual functions "
    "thus parsing the eclass only once per EBD processor.  Disabling "
    "this optimization via this option results in ~50%% slower "
    "regeneration. Disable it only if you suspect the optimization "
    "is somehow causing issues.")
regen.add_argument("--threads", "-t", type=int,
    default=commandline.DelayedValue(_get_default_jobs, 100),
    help="number of threads to use for regeneration.  Defaults to using all "
    "available processors")
regen.add_argument("repo", action=commandline.StoreConfigObject,
    config_type='repo', help="repository to regenerate caches for")
@regen.bind_main_func
def regen_main(options, out, err):
    """Regenerate a repository cache."""
    start_time = time()
    # HACK: store this here so we can assign to it from inside def passthru.
    options.count = 0
    if not options.disable_eclass_preloading:
        processor._global_enable_eclass_preloading = True
    if options.threads == 1:
        def passthru(iterable):
            for x in iterable:
                options.count += 1
                yield x
        regen_iter(passthru(options.repo), err)
    else:
        queue = Queue.Queue(options.threads * 2)
        kill = Event()
        kill.clear()
        def iter_queue(kill, qlist, timeout=0.25):
            while not kill.isSet():
                try:
                    yield qlist.get(timeout=timeout)
                except Queue.Empty:
                    continue
        regen_threads = [
            Thread(
                target=regen_iter, args=(iter_queue(kill, queue), err))
            for x in xrange(options.threads)]
        out.write('starting %d threads' % (options.threads,))
        try:
            for x in regen_threads:
                x.start()
            out.write('started')
            # now we feed the queue.
            for pkg in options.repo:
                options.count += 1
                queue.put(pkg)
        except Exception:
            kill.set()
            reclaim_threads(regen_threads, err)
            raise

        # by now, queue is fed. reliable for our uses since the queue
        # is only subtracted from.
        while not queue.empty():
            sleep(.5)
        kill.set()
        reclaim_threads(regen_threads, err)
        assert queue.empty()
    out.write("finished %d nodes in in %.2f seconds" % (options.count,
        time() - start_time))
    return 0


perl_rebuild = subparsers.add_parser("perl-rebuild",
    parents=(commandline.mk_argparser(add_help=False),),
    help="EXPERIMENTAL: perl-rebuild support for use after upgrading perl")
perl_rebuild.add_argument("new_version",
    help="the new perl version; 5.12.3 for example")
@perl_rebuild.bind_main_func
def perl_rebuild_main(options, out, err):

    path = pjoin(options.domain.root, "usr/lib/perl5",
        options.new_version)
    if not os.path.exists(path):
        err.write("version %s doesn't seem to be installed; can't find it at %r" %
            (options.new_version, path))
        return 1

    base = pjoin(options.domain.root, "/usr/lib/perl5")
    potential_perl_versions = [x.replace(".", "\.") for x in listdir_dirs(base)
        if x.startswith("5.") and x != options.new_version]

    if len(potential_perl_versions) == 1:
        subpattern = potential_perl_versions[0]
    else:
        subpattern = "(?:%s)" % ("|".join(potential_perl_versions),)
    matcher = re.compile("/usr/lib(?:64|32)?/perl5/(?:%s|vendor_perl/%s)" %
        (subpattern, subpattern)).match

    for pkg in options.domain.all_livefs_repos:
        contents = getattr(pkg, 'contents', ())
        if not contents:
            continue
        # scan just directories...
        for fsobj in contents.iterdirs():
            if matcher(fsobj.location):
                out.write("%s" % (pkg.unversioned_atom,))
                break
    return 0


env_update = subparsers.add_parser("env-update", help="update env.d and ldconfig",
    parents=(commandline.mk_argparser(add_help=False),))
env_update.add_argument("--skip-ldconfig", action='store_true', default=False,
    help="do not update etc/ldso.conf and ld.so.cache")
@env_update.bind_main_func
def env_update_main(options, out, err):
    root = getattr(options.domain, 'root', None)
    if root is None:
        err.write("domain specified lacks a root seting; is it a virtual or rmeote domain?")
        return 1

    out.write("updating env for %r..." % (root,))
    triggers.perform_env_update(root,
        skip_ldso_update=options.skip_ldconfig)
    if not options.skip_ldconfig:
        out.write("update ldso cache/elf hints for %r..." % (root,))
        merge_triggers.update_elf_hints(root)
    return 0
