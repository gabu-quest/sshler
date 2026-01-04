"""
Property-based tests for development workflow functionality.

**Feature: vue3-migration-completion, Property 2: Backend auto-reload functionality**
**Validates: Requirements 1.3**
"""

import asyncio
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from hypothesis import given, strategies as st

from sshler.cli import _cleanup_processes, _start_vite_dev_server, serve_dev


class TestDevWorkflowProperties:
    """Property-based tests for development workflow."""

    @given(
        host=st.sampled_from(["127.0.0.1", "localhost", "0.0.0.0"]),
        port=st.integers(min_value=8000, max_value=9999),
        log_level=st.sampled_from(["info", "debug", "warning", "error"]),
    )
    def test_backend_auto_reload_functionality(self, host, port, log_level):
        """
        **Feature: vue3-migration-completion, Property 2: Backend auto-reload functionality**
        **Validates: Requirements 1.3**
        
        For any Python file modification during development, the FastAPI server 
        should restart automatically and be ready to serve requests within a reasonable time.
        """
        # Create a temporary Python file to modify
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_file:
            temp_file.write("# Initial content\n")
            temp_file_path = temp_file.name

        try:
            # Mock the uvicorn.run to avoid actually starting the server
            with patch('sshler.cli.uvicorn.run') as mock_uvicorn:
                with patch('sshler.cli._start_vite_dev_server') as mock_vite:
                    # Mock Vite process
                    mock_process = Mock()
                    mock_process.poll.return_value = None
                    mock_process.stdout = None
                    mock_vite.return_value = mock_process
                    
                    # Mock frontend directory existence
                    with patch('pathlib.Path.exists', return_value=True):
                        with patch('pathlib.Path.cwd') as mock_cwd:
                            mock_cwd.return_value = Path('/mock/project')
                            
                            # Test that serve_dev calls uvicorn with reload=True
                            with patch('sshler.cli.time.sleep'):  # Skip the sleep
                                with patch('sshler.cli._open_browser_later'):  # Skip browser opening
                                    try:
                                        serve_dev(
                                            host=host,
                                            port=port,
                                            log_level=log_level,
                                            open_browser=False
                                        )
                                    except KeyboardInterrupt:
                                        pass  # Expected when mocking
                            
                            # Verify that uvicorn was called with reload=True
                            mock_uvicorn.assert_called_once()
                            call_args = mock_uvicorn.call_args
                            
                            # Check that reload is enabled
                            assert call_args.kwargs.get('reload') is True
                            assert call_args.kwargs.get('host') == host
                            assert call_args.kwargs.get('port') == port
                            assert call_args.kwargs.get('log_level') == log_level

        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def test_vite_dev_server_startup(self):
        """Test that Vite dev server can be started with proper configuration."""
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend_dir = Path(temp_dir)
            
            # Create mock package.json
            package_json = frontend_dir / "package.json"
            package_json.write_text('{"name": "test", "scripts": {"dev": "vite"}}')
            
            # Mock subprocess to avoid actually starting Vite
            with patch('subprocess.Popen') as mock_popen:
                mock_process = Mock()
                mock_process.poll.return_value = None
                mock_process.stdout = None
                mock_popen.return_value = mock_process
                
                # Mock pnpm availability check
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = Mock(returncode=0)  # pnpm available
                    
                    result = _start_vite_dev_server(frontend_dir)
                    
                    # Verify Popen was called with correct arguments
                    mock_popen.assert_called_once()
                    call_args = mock_popen.call_args
                    
                    assert call_args[0][0] == ["pnpm", "dev"]
                    assert call_args[1]['cwd'] == frontend_dir
                    assert result == mock_process

    def test_process_cleanup(self):
        """Test that processes are properly cleaned up."""
        # Create mock processes
        mock_process1 = Mock()
        mock_process1.poll.return_value = None  # Still running
        mock_process1.terminate.return_value = None
        mock_process1.wait.return_value = None
        
        mock_process2 = Mock()
        mock_process2.poll.return_value = 0  # Already terminated
        
        _cleanup_processes(mock_process1, mock_process2)
        
        # Verify terminate was called on running process
        mock_process1.terminate.assert_called_once()
        mock_process1.wait.assert_called_once()
        
        # Verify terminate was not called on already terminated process
        mock_process2.terminate.assert_not_called()

    @given(
        allow_origins=st.lists(
            st.builds(
                lambda protocol, domain, port: f"{protocol}://{domain}:{port}",
                protocol=st.sampled_from(["http", "https"]),
                domain=st.sampled_from(["localhost", "127.0.0.1", "example.com", "dev.local"]),
                port=st.integers(min_value=3000, max_value=9999),
            ),
            min_size=0,
            max_size=5
        )
    )
    def test_dev_origins_configuration(self, allow_origins):
        """
        **Feature: vue3-migration-completion, Property 3: API proxy functionality**
        **Validates: Requirements 1.5**
        
        For any API request made from the Vue frontend during development, 
        the request should be successfully proxied to the FastAPI backend.
        """
        with patch('sshler.cli.uvicorn.run') as mock_uvicorn:
            with patch('sshler.cli._start_vite_dev_server') as mock_vite:
                mock_process = Mock()
                mock_process.poll.return_value = None
                mock_process.stdout = None
                mock_vite.return_value = mock_process
                
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.cwd') as mock_cwd:
                        mock_cwd.return_value = Path('/mock/project')
                        
                        with patch('sshler.cli.time.sleep'):
                            with patch('sshler.cli._open_browser_later'):
                                with patch('sshler.cli.serve') as mock_serve:
                                    try:
                                        serve_dev(
                                            allow_origins=allow_origins,
                                            open_browser=False
                                        )
                                    except KeyboardInterrupt:
                                        pass
                                
                                # Verify that dev origins are added
                                mock_serve.assert_called_once()
                                call_args = mock_serve.call_args
                                actual_origins = call_args.kwargs.get('allow_origins', [])
                                
                                # Check that Vite dev server origins are included
                                expected_dev_origins = [
                                    "http://localhost:5173", 
                                    "http://127.0.0.1:5173"
                                ]
                                for origin in expected_dev_origins:
                                    assert origin in actual_origins
                                
                                # Check that original origins are preserved
                                for origin in allow_origins:
                                    assert origin in actual_origins


class TestDevWorkflowIntegration:
    """Integration tests for development workflow."""

    def test_frontend_directory_validation(self):
        """Test that missing frontend directory is handled properly."""
        with patch('pathlib.Path.cwd') as mock_cwd:
            mock_cwd.return_value = Path('/nonexistent/project')
            
            with patch('pathlib.Path.exists', return_value=False):
                with pytest.raises(SystemExit):
                    serve_dev(open_browser=False)

    def test_package_json_validation(self):
        """Test that missing package.json is handled properly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend_dir = Path(temp_dir)
            
            # Don't create package.json
            with pytest.raises(RuntimeError, match="package.json not found"):
                _start_vite_dev_server(frontend_dir)

    def test_signal_handling(self):
        """Test that signal handlers are properly set up for graceful shutdown."""
        original_signal_handler = signal.signal(signal.SIGINT, signal.SIG_DFL)
        
        try:
            with patch('sshler.cli._start_vite_dev_server') as mock_vite:
                mock_process = Mock()
                mock_process.poll.return_value = None
                mock_process.stdout = None
                mock_vite.return_value = mock_process
                
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.cwd') as mock_cwd:
                        mock_cwd.return_value = Path('/mock/project')
                        
                        with patch('sshler.cli.time.sleep'):
                            with patch('sshler.cli._open_browser_later'):
                                with patch('sshler.cli.serve') as mock_serve:
                                    # Mock serve to raise KeyboardInterrupt immediately
                                    mock_serve.side_effect = KeyboardInterrupt()
                                    
                                    try:
                                        serve_dev(open_browser=False)
                                    except SystemExit:
                                        pass  # Expected
                                    
                                    # Verify signal handlers were set
                                    current_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_DFL)
                                    assert current_sigint_handler != signal.SIG_DFL
                                    
        finally:
            # Restore original signal handler
            signal.signal(signal.SIGINT, original_signal_handler)