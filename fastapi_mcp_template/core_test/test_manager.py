"""Test manager for dynamically loading and running tests."""

import os
import importlib.util
import inspect
import asyncio
from typing import Dict, List, Any, Optional
from pathlib import Path


class TestManager:
    """Manages dynamic test loading and execution."""
    
    def __init__(self, tests_directory: str = "/app/tests"):
        self.tests_directory = Path(tests_directory)
        self.registered_tests: Dict[str, Dict[str, Any]] = {}
        self.test_instances: Dict[str, Any] = {}
        self.test_base = self._load_test_base()
    
    def _has_test_methods(self, cls) -> bool:
        """Check if a class has test methods."""
        return any(method.startswith('test_') for method in dir(cls))
    
    def _load_test_base(self):
        """Dynamically load TestBase."""
        try:
            # Try to import TestBase from test_base module
            test_base_path = Path(__file__).parent / "test_base.py"
            if test_base_path.exists():
                spec = importlib.util.spec_from_file_location("test_base", test_base_path)
                test_base_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(test_base_module)
                
                if hasattr(test_base_module, 'TestBase'):
                    return test_base_module.TestBase()
            
            # Fallback to a minimal test base
            class MinimalTestBase:
                def log_test_info(self, message: str):                    print(f"[TEST INFO] {message}")
                def log_test_error(self, message: str):
                    print(f"[TEST ERROR] {message}")
            
            return MinimalTestBase()
            
        except Exception as e:
            print(f"Could not load TestBase: {e}")
            return None
    
    async def discover_tests(self) -> List[Dict[str, Any]]:
        """Discover all tests in the tests directory."""
        tests = []
        
        if not self.tests_directory.exists():
            self.test_base.log_test_info(f"Tests directory does not exist: {self.tests_directory}")
            return tests
        
        # Scan for test files
        for file_path in self.tests_directory.glob("test_*.py"):
            try:
                test_info = await self._load_test_file(file_path)
                if test_info:
                    tests.append(test_info)
                    self.test_base.log_test_info(f"Discovered test: {test_info['name']}")
            except Exception as e:
                self.test_base.log_test_error(f"Failed to load test {file_path}: {e}")
        
        return tests
    
    async def _load_test_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Load a single test file."""
        try:
            # Import the test module
            spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)            
            # Find test classes
            test_classes = []
            test_functions = []
            
            for name, obj in inspect.getmembers(module):
                if inspect.isclass(obj):
                    # Check if it's a test class (inherits from unittest.TestCase or has test methods)
                    if (hasattr(obj, 'setUp') or hasattr(obj, 'test_') or 
                        name.startswith('Test') or self._has_test_methods(obj)):
                        test_classes.append({
                            'name': name,
                            'class': obj,
                            'methods': [m for m in dir(obj) if m.startswith('test_')]
                        })
                elif inspect.isfunction(obj) and name.startswith('test_'):
                    test_functions.append({
                        'name': name,
                        'function': obj
                    })
            
            if test_classes or test_functions:
                test_info = {
                    'name': file_path.stem,
                    'file_path': str(file_path),
                    'module': module,
                    'test_classes': test_classes,
                    'test_functions': test_functions,
                    'description': getattr(module, '__doc__', f"Tests from {file_path.name}")
                }
                
                self.registered_tests[file_path.stem] = test_info
                return test_info
            
        except Exception as e:
            self.test_base.log_test_error(f"Error loading test file {file_path}: {e}")
            return None
    
    async def run_test(self, test_name: str, specific_test: Optional[str] = None) -> Dict[str, Any]:
        """Run a specific test or all tests in a test file."""
        if test_name not in self.registered_tests:
            raise ValueError(f"Test {test_name} not found")
        
        test_info = self.registered_tests[test_name]
        results = {
            'test_name': test_name,
            'passed': 0,
            'failed': 0,
            'errors': [],
            'details': []
        }
        
        # Run test classes
        for test_class_info in test_info['test_classes']:
            try:
                test_class = test_class_info['class']
                
                # If specific test method is requested
                if specific_test:
                    if specific_test in test_class_info['methods']:
                        result = await self._run_test_method(test_class, specific_test)
                        results['details'].append(result)
                        if result['success']:
                            results['passed'] += 1
                        else:
                            results['failed'] += 1
                            results['errors'].append(result['error'])
                else:
                    # Run all test methods
                    for method_name in test_class_info['methods']:
                        result = await self._run_test_method(test_class, method_name)
                        results['details'].append(result)
                        if result['success']:
                            results['passed'] += 1
                        else:
                            results['failed'] += 1
                            results['errors'].append(result['error'])
                            
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Error running test class {test_class_info['name']}: {e}")
        
        # Run test functions
        for test_func_info in test_info['test_functions']:
            try:
                if not specific_test or test_func_info['name'] == specific_test:
                    result = await self._run_test_function(test_func_info['function'], test_func_info['name'])
                    results['details'].append(result)
                    if result['success']:
                        results['passed'] += 1
                    else:
                        results['failed'] += 1
                        results['errors'].append(result['error'])
                        
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Error running test function {test_func_info['name']}: {e}")
        
        return results
    
    async def _run_test_method(self, test_class, method_name: str) -> Dict[str, Any]:
        """Run a single test method."""
        try:
            # Create test instance
            test_instance = test_class()
            
            # Setup if available
            if hasattr(test_instance, 'setUp'):
                test_instance.setUp()
            
            # Get the test method
            test_method = getattr(test_instance, method_name)
            
            # Run the test method
            if asyncio.iscoroutinefunction(test_method):
                await test_method()
            else:
                test_method()
            
            # Teardown if available
            if hasattr(test_instance, 'tearDown'):
                test_instance.tearDown()
            
            return {
                'name': f"{test_class.__name__}.{method_name}",
                'success': True,
                'error': None
            }
            
        except Exception as e:
            return {
                'name': f"{test_class.__name__}.{method_name}",
                'success': False,
                'error': str(e)
            }
    
    async def _run_test_function(self, test_function, function_name: str) -> Dict[str, Any]:
        """Run a single test function."""
        try:
            if asyncio.iscoroutinefunction(test_function):
                await test_function()
            else:
                test_function()
            
            return {
                'name': function_name,
                'success': True,
                'error': None
            }
            
        except Exception as e:
            return {
                'name': function_name,
                'success': False,
                'error': str(e)
            }
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all discovered tests."""
        await self.discover_tests()
        
        all_results = {
            'total_tests': len(self.registered_tests),
            'passed': 0,
            'failed': 0,
            'errors': [],
            'test_results': {}
        }
        
        for test_name in self.registered_tests:
            result = await self.run_test(test_name)
            all_results['test_results'][test_name] = result
            all_results['passed'] += result['passed']
            all_results['failed'] += result['failed']
            all_results['errors'].extend(result['errors'])
        
        return all_results
    
    def list_tests(self) -> List[Dict[str, Any]]:
        """List all registered tests."""
        return [
            {
                'name': name,
                'description': info['description'],
                'file_path': info['file_path'],
                'test_classes': [tc['name'] for tc in info['test_classes']],
                'test_functions': [tf['name'] for tf in info['test_functions']]
            }
            for name, info in self.registered_tests.items()
        ]
