# Copyright: 2006-2011 Brian Harring <ferringb@gmail.com>
# Copyright: 2006-2007 Marien Zwart <marienz@gentoo.org>
# License: BSD/GPL2

import argparse
import errno
from functools import partial
import os
import pty
import textwrap

from snakeoil import compatibility

from pkgcore.config import central, errors
from pkgcore.test import TestCase, silence_logging
from pkgcore.test.scripts import helpers
from pkgcore.util import commandline

# Careful: the tests should not hit a load_config() call!

if compatibility.is_py3k:
    import io
else:
    from StringIO import StringIO


def sect():
    """Just a no-op to use as configurable class."""


def mk_config(*args, **kwds):
    return central.CompatConfigManager(
        central.ConfigManager(*args, **kwds))


class ArgparseOptionsTest(TestCase):

    _parser_func = staticmethod(commandline.mk_argparser)

    def _parser(self, **kwargs):
        # suppress config/domain by default.
        kwargs.setdefault("domain", False)
        kwargs.setdefault("config", False)
        return self._parser_func(**kwargs)

    @silence_logging
    def test_debug(self):
        namespace = self._parser().parse_args(["--debug"])
        self.assertTrue(namespace.debug)
        namespace = self._parser().parse_args([])
        self.assertFalse(namespace.debug)

        # ensure the option isn't there if disabled.
        namespace = self._parser(debug=False).parse_args([])
        self.assertFalse(hasattr(namespace, 'debug'))

        # ensure debug is pushed down into config.
        namespace = self._parser(config=True).parse_args(["--empty-config"])
        self.assertFalse(namespace.config.debug)

        namespace = self._parser(config=True).parse_args(
            ["--empty-config", "--debug"])
        self.assertTrue(namespace.config.debug)

    def test_bool_type(self):
        parser = helpers.mangle_parser(commandline.ArgumentParser())
        parser.add_argument(
            "--testing", action=commandline.StoreBool, default=None)

        for raw_val in ("n", "no", "false"):
            for allowed in (raw_val.upper(), raw_val.lower()):
                namespace = parser.parse_args(['--testing=' + allowed])
                self.assertEqual(
                    namespace.testing, False,
                    msg="for --testing=%s, got %r, expected False" %
                        (allowed, namespace.testing))

        for raw_val in ("y", "yes", "true"):
            for allowed in (raw_val.upper(), raw_val.lower()):
                namespace = parser.parse_args(['--testing=' + allowed])
                self.assertEqual(
                    namespace.testing, True,
                    msg="for --testing=%s, got %r, expected False" %
                        (allowed, namespace.testing))

        try:
            parser.parse_args(["--testing=invalid"])
        except helpers.Error:
            pass
        else:
            self.fail("no error message thrown for --testing=invalid")


class _Trigger(argparse.Action):

    def __call__(self, parser, namespace, values, option_string=None):
        """Fake a config load."""

        # HACK: force skipping the actual config loading. Might want
        # to do something more complicated here to allow testing if
        # --empty-config actually works.
        namespace.empty_config = True


class ModifyConfigTest(TestCase, helpers.ArgParseMixin):

    parser = commandline.mk_argparser(domain=False)
    parser.add_argument('--trigger', nargs=0, action=_Trigger)

    def parse(self, *args, **kwargs):
        """Overridden to allow the load_config call."""
        # argparse needs a list (it does make a copy, but it uses [:]
        # to do it, which is a noop on a tuple).
        namespace = self.parser.parse_args(list(args))

        # HACK: force skipping the actual config loading. Might want
        # to do something more complicated here to allow testing if
        # --empty-config actually works.
        namespace.empty_config = True

        return namespace

    def test_empty_config(self):
        self.assertTrue(self.parse('--empty-config', '--trigger'))

    def test_modify_config(self):
        namespace = self.parse(
            '--empty-config', '--new-config',
            'foo', 'class', 'pkgcore.test.util.test_commandline.sect',
            '--trigger')
        self.assertTrue(namespace.config.collapse_named_section('foo'))

        namespace = self.parse(
            '--empty-config', '--new-config',
            'foo', 'class', 'pkgcore.test.util.test_commandline.missing',
            '--add-config', 'foo', 'class',
            'pkgcore.test.util.test_commandline.sect',
            '--trigger')
        self.assertTrue(namespace.config.collapse_named_section('foo'))

        namespace = self.parse(
            '--empty-config',
            '--add-config', 'foo', 'inherit', 'missing',
            '--trigger')
        self.assertRaises(
            errors.ConfigurationError,
            namespace.config.collapse_named_section, 'foo')


if compatibility.is_py3k:
    # This dance is currently necessary because commandline.main wants
    # an object it can write text to (to write error messages) and
    # pass to PlainTextFormatter, which wants an object it can write
    # bytes to. If we pass it a TextIOWrapper then the formatter can
    # unwrap it to get at the byte stream (a BytesIO in our case).
    def _stream_and_getvalue():
        bio = io.BytesIO()
        f = io.TextIOWrapper(bio, line_buffering=True)

        def getvalue():
            return bio.getvalue().decode('ascii')
        return f, getvalue
else:
    def _stream_and_getvalue():
        bio = StringIO()
        return bio, bio.getvalue


class MainTest(TestCase):

    def assertMain(self, status, outtext, errtext, subcmds, *args, **kwargs):
        out, out_getvalue = _stream_and_getvalue()
        err, err_getvalue = _stream_and_getvalue()
        try:
            commandline.main(subcmds, outfile=out, errfile=err, *args, **kwargs)
        except SystemExit as e:
            self.assertEqual(errtext, err_getvalue())
            self.assertEqual(outtext, out_getvalue())
            self.assertEqual(
                status, e.args[0],
                msg="expected status %r, got %r" % (status, e.args[0]))
        else:
            self.fail('no exception raised')

    def test_method_run(self):
        argparser = commandline.mk_argparser(suppress=True)
        argparser.add_argument("--foon")

        @argparser.bind_main_func
        def run(options, out, err):
            out.write("args: %s" % (options.foon,))
            return 0

        self.assertMain(
            0, 'args: dar\n', '',
            argparser, args=['--foon', 'dar'])

    def test_argparse_with_invalid_args(self):
        argparser = commandline.mk_argparser(suppress=True, add_help=False)

        @argparser.bind_main_func
        def main(options, out, err):
            pass

        # this is specifically asserting that if given a positional arg (the '1'),
        # which isn't valid in our argparse setup, it returns exit code -10.
        self.assertMain(-10, '', '', argparser, ['1'])

    def test_configuration_error(self):
        argparser = commandline.mk_argparser(suppress=True)

        @argparser.bind_main_func
        def error_main(options, out, err):
            raise errors.ConfigurationError('bork')

        self.assertMain(
            -10, '', 'Error in configuration:\n bork\n', argparser, [])

    def _get_pty_pair(self, encoding='ascii'):
        master_fd, slave_fd = pty.openpty()
        master = os.fdopen(master_fd, 'rb', 0)
        out = os.fdopen(slave_fd, 'wb', 0)
        if compatibility.is_py3k:
            # note that 2to3 converts the global StringIO import to io
            master = io.TextIOWrapper(master)
            out = io.TextIOWrapper(out)
        return master, out

    def test_tty_detection(self):
        argparser = commandline.mk_argparser(
            config=False, domain=False, color=True, debug=False,
            quiet=False, verbose=False, version=False)

        @argparser.bind_main_func
        def main(options, out, err):
            for f in (out, err):
                name = f.__class__.__name__
                if name.startswith("native_"):
                    name = name[len("native_"):]
                f.write(name, autoline=False)

        for args, out_kind, err_kind in (
                ([], 'TerminfoFormatter', 'PlainTextFormatter'),
                (['--color=n'], 'PlainTextFormatter', 'PlainTextFormatter'),
                ):
            master, out = self._get_pty_pair()
            err, err_getvalue = _stream_and_getvalue()

            try:
                commandline.main(argparser, args, out, err)
            except SystemExit as e:
                # Important, without this reading the master fd blocks.
                out.close()
                self.assertEqual(None, e.args[0])

                # There can be an xterm title update after this.
                #
                # XXX: Workaround py34 making it harder to read all data from a
                # pty due to issue #21090 (http://bugs.python.org/issue21090).
                out_name = ''
                try:
                    while True:
                        out_name += os.read(master.fileno(), 1).decode()
                except OSError as e:
                    if e.errno == errno.EIO:
                        pass
                    else:
                        raise

                master.close()
                self.assertTrue(
                    out_name.startswith(out_kind) or out_name == 'PlainTextFormatter',
                    'expected %r, got %r' % (out_kind, out_name))
                self.assertEqual(err_kind, err_getvalue())
            else:
                self.fail('no exception raised')
