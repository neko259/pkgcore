# Copyright: 2006-2011 Brian Harring <ferringb@gmail.com>
# License: GPL2/BSD

"""
binpkg tar utilities
"""

import os
import stat

from snakeoil import compression
from snakeoil.compatibility import cmp, sorted_cmp
from snakeoil.currying import partial
from snakeoil.data_source import invokable_data_source
from snakeoil.tar import tarfile

from pkgcore.fs import contents
from pkgcore.fs.fs import fsFile, fsDir, fsSymlink, fsFifo, fsDev


known_compressors = {"bz2": tarfile.TarFile.bz2open,
    "gz": tarfile.TarFile.gzopen,
    None: tarfile.TarFile.open}


def write_set(contents_set, filepath, compressor='bzip2', absolute_paths=False,
              parallelize=False):
    if compressor == 'bz2':
        compressor = 'bzip2'

    tar_handle = None
    handle = compression.compress_handle(compressor, filepath,
        parallelize=parallelize)
    try:
        tar_handle = tarfile.TarFile(name=filepath, fileobj=handle, mode='w')
        add_contents_to_tarfile(contents_set, tar_handle)
    finally:
        if tar_handle is not None:
            tar_handle.close()
        handle.close()

def add_contents_to_tarfile(contents_set, tar_fd, absolute_paths=False):
    # first add directories, then everything else
    # this is just a pkgcore optimization, it prefers to see the dirs first.
    dirs = contents_set.dirs()
    dirs.sort()
    for x in dirs:
        tar_fd.addfile(fsobj_to_tarinfo(x, absolute_paths))
    del dirs
    for x in contents_set.iterdirs(invert=True):
        t = fsobj_to_tarinfo(x, absolute_paths)
        if t.isreg():
            tar_fd.addfile(t, fileobj=x.data.bytes_fileobj())
        else:
            tar_fd.addfile(t)


def archive_to_fsobj(src_tar):
    psep = os.path.sep
    for member in src_tar:
        d = {
            "uid":member.uid, "gid":member.gid,
            "mtime":member.mtime, "mode":member.mode}
        location = psep + member.name.strip(psep)
        if member.isdir():
            if member.name.strip(psep) == ".":
                continue
            yield fsDir(location, **d)
        elif member.isreg():
            d["data"] = invokable_data_source.wrap_function(partial(
                    src_tar.extractfile, member.name), returns_text=False,
                    returns_handle=True)
            # suppress hardlinks until the rest of pkgcore is updated for it.
            d["dev"] = None
            d["inode"] = None
            yield fsFile(location, **d)
        elif member.issym() or member.islnk():
            yield fsSymlink(location, member.linkname, **d)
        elif member.isfifo():
            yield fsFifo(location, **d)
        elif member.isdev():
            d["major"] = long(member.major)
            d["minor"] = long(member.minor)
            yield fsDev(location, **d)
        else:
            raise AssertionError(
                "unknown type %r, %r was encounted walking tarmembers" %
                    (member, member.type))

def fsobj_to_tarinfo(fsobj, absolute_path=True):
    t = tarfile.TarInfo()
    if fsobj.is_reg:
        t.type = tarfile.REGTYPE
        t.size = fsobj.chksums["size"]
    elif fsobj.is_dir:
        t.type = tarfile.DIRTYPE
    elif fsobj.is_sym:
        t.type = tarfile.SYMTYPE
        t.linkname = fsobj.target
    elif fsobj.is_fifo:
        t.type = tarfile.FIFOTYPE
    elif fsobj.is_dev:
        if stat.S_ISCHR(fsobj.mode):
            t.type = tarfile.CHRTYPE
        else:
            t.type = tarfile.BLKTYPE
        t.devmajor = fsobj.major
        t.devminor = fsobj.minor
    t.name = fsobj.location
    if not absolute_path:
        t.name = './%s' % (fsobj.location.lstrip("/"),)
    t.mode = fsobj.mode
    t.uid = fsobj.uid
    t.gid = fsobj.gid
    t.mtime = fsobj.mtime
    return t


def generate_contents(filepath, compressor="bz2", parallelize=True):
    """
    generate a contentset from a tarball

    :param filepath: string path to location on disk
    :param compressor: defaults to bz2; decompressor to use, see
        :obj:`known_compressors` for list of valid compressors
    """

    if compressor == 'bz2':
        compressor = 'bzip2'

    tar_handle = None
    handle = compression.decompress_handle(compressor, filepath,
        parallelize=parallelize)

    try:
        tar_handle = tarfile.TarFile(name=filepath, fileobj=handle, mode='r')
    except tarfile.ReadError as e:
        if not e.message.endswith("empty header"):
            raise
        tar_handle = []
    return convert_archive(tar_handle)


def convert_archive(archive):
    # regarding the usage of del in this function... bear in mind these sets
    # could easily have 10k -> 100k entries in extreme cases; thus the del
    # usage, explicitly trying to ensure we don't keep refs long term.

    # this one is a bit fun.
    raw = list(archive_to_fsobj(archive))
    # we use the data source as the unique key to get position.
    files_ordering = list(enumerate(x for x in raw if x.is_reg))
    files_ordering = {x.data: idx for idx, x in files_ordering}
    t = contents.contentsSet(raw, mutable=True)
    del raw, archive

    # first rewrite affected syms.
    raw_syms = t.links()
    syms = contents.contentsSet(raw_syms)
    while True:
        for x in sorted(syms):
            affected = syms.child_nodes(x.location)
            if not affected:
                continue
            syms.difference_update(affected)
            syms.update(affected.change_offset(x.location, x.resolved_target))
            del affected
            break
        else:
            break

    t.difference_update(raw_syms)
    t.update(syms)

    del raw_syms
    syms = sorted(syms, reverse=True)
    # ok, syms are correct.  now we get the rest.
    # we shift the readds into a separate list so that we don't reinspect
    # them on later runs; this slightly reduces the working set.
    additions = []
    for x in syms:
        affected = t.child_nodes(x.location)
        if not affected:
            continue
        t.difference_update(affected)
        additions.extend(affected.change_offset(x.location, x.resolved_target))

    t.update(additions)
    t.add_missing_directories()

    # finally... an insane sort.
    def sort_func(x, y):
        if x.is_dir:
            if not y.is_dir:
                return -1
            return cmp(x, y)
        elif y.is_dir:
            return +1
        elif x.is_reg:
            if y.is_reg:
                return cmp(files_ordering[x.data],
                    files_ordering[y.data])
            return +1
        elif y.is_reg:
            return -1
        return cmp(x, y)

    return contents.OrderedContentsSet(sorted_cmp(t, sort_func), mutable=False)
