import os
import sys
import unittest
from inspect import isclass, isfunction, ismethod
from nose.case import Failure, FunctionTestCase, MethodTestCase
from nose.config import Config
from nose.context import FixtureContext
from nose.importer import Importer, add_path, remove_path
from nose.selector import TestAddress
from nose.util import cmp_lineno, getpackage, isgenerator, ispackage, \
    resolve_name
from suite import LazySuite, ContextSuiteFactory

class TestLoader(unittest.TestLoader):

    def __init__(self, config=None, context=None, importer=None,
                 working_dir=None):
        # FIXME would get selector too
        if config is None:
            config = Config()
        if context is None:
            context = FixtureContext
        if importer is None:
            importer = Importer(config=config)
        if working_dir is None:
            working_dir = os.getcwd()
        self.config = config
        self.context = context
        self.importer = importer
        self.working_dir = working_dir
        if config.addPaths:
            add_path(working_dir)        
        self.suiteClass = ContextSuiteFactory(context, config=config)
        unittest.TestLoader.__init__(self)        

    def loadTestsFromTestClass(self, cls):
        """Load tests from a test class that is *not* a unittest.TestCase
        subclass.

        In this case, we can't depend on the class's `__init__` taking method
        name arguments, so we have to compose a MethodTestCase for each
        method in the class that looks testlike.        
        """
        tests = []
        for entry in dir(cls):
            # FIXME use a selector
            if not entry.startswith('test'):
                continue
            item = getattr(cls, entry, None)
            if ismethod(item):
                tests.append(self.makeTest(item, cls))
        return self.suiteClass(tests, parent=cls)

    def loadTestsFromDir(self, path):
        print "load from dir %s" % path

        if self.config.addPaths:
            paths_added = add_path(path)
            
        for entry in os.listdir(path):
            if entry.startswith('.') or entry.startswith('_'):
                continue
            print "at %s" % entry

            entry_path = os.path.abspath(os.path.join(path, entry))
            is_test = entry.startswith('test') # FIXME use selector
            is_file = os.path.isfile(entry_path)
            if is_file:
                is_dir = False
            else:
                is_dir = os.path.isdir(entry_path)
            is_package = ispackage(entry_path)
            if is_test and is_file and entry.endswith('.py'):
                # FIXME this needs to be a full path name?
                yield self.loadTestsFromName(entry_path)
            elif is_dir:
                if is_package:
                    # Load the entry as a package: given the full path,
                    # loadTestsFromName() will figure it out
                    # FIXME should pass the full path name here and
                    # let TestAddress figure it out?
                    yield self.loadTestsFromName(entry_path)
                elif is_test:
                    # Another test dir in this one: recurse lazily
                    yield LazySuite(
                        lambda: self.loadTestsFromDir(entry_path))
        # FIXME pop dir off of sys.path -- ideally, but we can't have
        # try: finally: around a generator and we can't guarantee that
        # a generator will be called often enough to do the pop
        
        # FIXME give plugins a chance?

        # pop paths
        if self.config.addPaths:
            map(remove_path, paths_added)

    def loadTestsFromFile(self, filename):
        # only called for non-module files
        # FIXME give plugins a chance
        pass

    def loadTestsFromGenerator(self, generator, module):
        """Lazy-load tests from a generator function. The generator function
        may yield either:

        * a callable, or
        * a function name resolvable within the same module
        """
        def generate(g=generator, m=module):
            for test in g():
                try:
                    test_func, arg = (test[0], test[1:])
                except ValueError:
                    test_func, arg = test[0], tuple()
                if not callable(test_func):
                    test_func = getattr(m, test_func)
                yield FunctionTestCase(test_func, arg=arg, descriptor=g)
        return self.suiteClass(generate)

    def loadTestsFromGeneratorMethod(self, generator, cls):
        """Lazy-load tests from a generator method.

        This is more complicated than loading from a generator function,
        since a generator method may yield:

        * a function
        * an unbound method, or
        * a method name
        """
        # convert the unbound generator method
        # into a bound method so it can be called below
        cls = generator.im_class
        inst = cls()
        method = generator.__name__
        generator = getattr(inst, method)

        def generate(g=generator, c=cls):
            for test in g():
                try:
                    test_func, arg = (test[0], test[1:])
                except ValueError:
                    test_func, arg = test[0], tuple()
                if not callable(test_func):
                    test_func = getattr(c, test_func)
                if ismethod(test_func):
                    yield MethodTestCase(test_func, arg=arg, descriptor=g)
                elif isfunction(test_func):
                    # In this case we're forcing the 'MethodTestCase'
                    # to run the inline function as its test call,
                    # but using the generator method as the 'method of
                    # record' (so no need to pass it as the descriptor)
                    yield MethodTestCase(g, test=test_func, arg=arg)
                else:
                    yield Failure(TypeError,
                                  "%s is not a function or method" % test_func)
        return self.suiteClass(generate)

    def loadTestsFromModule(self, module):
        print "Load from module %s" % module.__name__
        tests = []
        test_classes = []
        test_funcs = []
        for item in dir(module):
            test = getattr(module, item, None)
            print "Check %s (%s) in %s" % (item, test, module.__name__)
            if isclass(test):
                # FIXME use a selector
                if (issubclass(test, unittest.TestCase)
                    or test.__name__.lower().startswith('test')):
                    test_classes.append(test)
            elif isfunction(test) and item.lower().startswith('test'):
                # FIXME use selector
                test_funcs.append(test)
        test_classes.sort(lambda a, b: cmp(a.__name__, b.__name__))
        test_funcs.sort(cmp_lineno)
        tests = map(lambda t: self.makeTest(t, parent=module),
                    test_classes + test_funcs)

        # Now, descend into packages
        paths = getattr(module, '__path__', [])
        for path in paths:
            tests.extend(self.loadTestsFromDir(path))
        # FIXME give plugins a chance
        return self.suiteClass(tests, parent=module)
    
    def loadTestsFromName(self, name, module=None):
        # FIXME refactor this method into little bites
        # FIXME would pass working dir too
        suite = self.suiteClass
        addr = TestAddress(name, working_dir=self.working_dir)
        print "load from %s (%s) (%s)" % (name, addr, module)
        print addr.filename, addr.module, addr.call
        
        if module:
            # Two cases:
            #  name is class.foo
            #    The addr will be incorrect, since it thinks class.foo is
            #    a dotted module name. It's actually a dotted attribute
            #    name. In this case we want to use the full submitted
            #    name as the name to load from the module.
            #  name is module:class.foo
            #    The addr will be correct. The part we want is the part after
            #    the :, which is in addr.call.
            if addr.call:
                name = addr.call
            parent, obj = self.resolve(name, module)
            return suite([self.makeTest(obj, parent)], parent=module)
        else:
            if addr.module:
                try:
                    module = self.importer.import_from_path(
                        addr.filename, addr.module)
                except KeyboardInterrupt, SystemExit:
                    raise
                except:
                    exc = sys.exc_info()
                    return suite([Failure(*exc)])
                if addr.call:
                    return self.loadTestsFromName(addr.call, module)
                else:
                    return self.loadTestsFromModule(module)
            elif addr.filename:
                path = addr.filename
                if addr.call:
                    # FIXME need to filter the returned tests
                    raise Exception("Need to filter the returned tests")
                else:
                    if os.path.isdir(path):
                        # In this case we *can* be lazy since we know
                        # that each module in the dir will be fully
                        # loaded before its tests are executed; we
                        # also know that we're not going to be asked
                        # to load from . and ./some_module.py *as part
                        # of this named test load*
                        return LazySuite(
                            lambda: self.loadTestsFromDir(path))
                    elif os.path.isfile(path):
                        return self.loadTestsFromFile(path)
            else:
                # just a function? what to do? I think it can only be
                # handled when module is not None
                return suite([
                    Failure(ValueError, "Unresolvable test name %s" % name)])
        # FIXME give plugins a chance?

    def makeTest(self, obj, parent=None):
        """Given a test object and its parent, return a unittest.TestCase
        instance that can be run as a test.
        """
        if isinstance(obj, unittest.TestCase):
            return obj
        elif isclass(obj):
            if issubclass(obj, unittest.TestCase):
                return self.loadTestsFromTestCase(obj)
            else:
                return self.loadTestsFromTestClass(obj)
        elif ismethod(obj):
            if parent is None:
                parent = obj.__class__
            if issubclass(parent, unittest.TestCase):
                return parent(obj.__name__)
            else:
                if isgenerator(obj):
                    return self.loadTestsFromGeneratorMethod(obj, parent)
                else:
                    return MethodTestCase(obj)
        elif isfunction(obj):
            if isgenerator(obj):
                return self.loadTestsFromGenerator(obj, parent)
            else:
                return FunctionTestCase(obj)
        else:
            # FIXME give plugins a chance
            return Failure(TypeError,
                           "Can't make a test from %s" % obj)

    def resolve(self, name, module):
#        parent, obj = resolve_name(name, module)
        obj = module
        parts = name.split('.')
        for part in parts:
            parent, obj = obj, getattr(obj, part, None)
        if obj is None:
            # no such test
            obj = Failure(ValueError, "No such test %s" % name)
        return parent, obj

defaultTestLoader = TestLoader