# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: GPL2

"""
wrap a repository, binding configuration to pkgs returned from the repository
"""

from pkgcore.repository import prototype
from pkgcore.package.conditionals import PackageWrapper
from pkgcore.util.currying import pre_curry


class tree(prototype.tree):
	configured = True

	def __init__(self, raw_repo, wrapped_attrs):

		"""
		@param raw_repo: repo to wrap
		@type raw_repo: L{pkgcore.repository.prototype.tree}
		@param wrapped_attrs: sequence of attrs to wrap for each pkg
		"""

		# yes, we're intentionally not using tree's init.
		# not perfect I know.
		self.raw_repo = raw_repo
		self.wrapped_attrs = wrapped_attrs
		self.attr_filters = frozenset(wrapped_attrs.keys() + [self.configurable])

	def _get_pkg_kwds(self, pkg):
		raise NotImplementedError()

	def package_class(self, pkg, *a):
		kwds = self._get_pkg_kwds(pkg)
		kwds.setdefault("attributes_to_wrap", self.wrapped_attrs)
		return PackageWrapper(pkg, self.configurable, **kwds)

	def __getattr__(self, attr):
		return getattr(self.raw_repo, attr)

	def itermatch(self, restrict, **kwds):
		kwds.setdefault("force", True)
		o = kwds.get("pkg_klass_override", None)
		if o is not None:
			kwds["pkg_klass_override"] = pre_curry(self.package_class, o)
		else:
			kwds["pkg_klass_override"] = self.package_class
		return self.raw_repo.itermatch(restrict, **kwds)

	itermatch.__doc__ = prototype.tree.itermatch.__doc__.replace(
		"@param", "@keyword").replace("@keyword restrict:", "@param restrict:")

	def __getitem__(self, key):
		return self.package_class(self.raw_repo[key])

	def __iter__(self):
		return (self.package_class(cpv) for cpv in self.raw_repo)
