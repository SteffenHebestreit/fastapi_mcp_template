"""
File to Markdown Converter Tool

This tool converts various file formats to Markdown using the MarkItDown library.
It's completely self-contained with no imports from the core service.
"""

import io
import base64
from typing import Dict, Any
from pathlib import Path

try:
    from markitdown import MarkItDown
except ImportError:
    MarkItDown = None


class ToolDefinition:
    """Definition structure for a tool."""
    
    def __init__(self, name: str, description: str, endpoint: str, tool_type: str, version: str = "1.0.0"):
        self.name = name
        self.description = description
        self.endpoint = endpoint
        self.tool_type = tool_type  # Now accepts any string
        self.version = version
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "endpoint": self.endpoint,
            "type": self.tool_type,  # Direct string value
            "version": self.version
        }


class ToolSchema:
    """Schema definition for tool parameters."""
    
    def __init__(self, properties: Dict[str, Any], required: list = None):
        self.properties = properties
        self.required = required or []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": self.properties,
            "required": self.required
        }


class ToolInterface:
    """Interface that tools must implement."""
    
    def get_definition(self) -> ToolDefinition:
        raise NotImplementedError
    
    def get_schema(self) -> ToolSchema:
        raise NotImplementedError
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


class FileToMarkdownConverter(ToolInterface):
    """Converts various file formats to Markdown."""
    
    def __init__(self, tool_base):
        self.tool_base = tool_base
        self.markitdown = None
        if MarkItDown:
            self._initialize_markitdown()

    def _initialize_markitdown(self):
        """Initialize MarkItDown with optional OpenAI integration."""
        try:
            # Get configuration from tool_base if available
            config = getattr(self.tool_base, 'config', {}) if self.tool_base else {}
            
            if self.tool_base and hasattr(self.tool_base, 'log_info'):
                self.tool_base.log_info(f"Config keys available: {list(config.keys()) if isinstance(config, dict) else 'None'}")
            
            # Check if LLM features are enabled
            enable_llm = config.get('markitdown_enable_llm', False)
            openai_api_key = config.get('openai_api_key')
            openai_base_url = config.get('openai_base_url')
            openai_model = config.get('openai_model', 'gpt-4o')
            
            # Log configuration values
            if self.tool_base and hasattr(self.tool_base, 'log_info'):
                self.tool_base.log_info(f"LLM enabled: {enable_llm}")
                self.tool_base.log_info(f"OpenAI API key present: {bool(openai_api_key)}")
                self.tool_base.log_info(f"OpenAI base URL: {openai_base_url}")
                self.tool_base.log_info(f"OpenAI model: {openai_model}")
            
            if enable_llm and openai_api_key:
                try:
                    from openai import OpenAI
                    
                    # Initialize OpenAI client
                    client_kwargs = {'api_key': openai_api_key}
                    if openai_base_url:
                        client_kwargs['base_url'] = openai_base_url
                    
                    client = OpenAI(**client_kwargs)
                      # Initialize MarkItDown with LLM support
                    try:
                        self.markitdown = MarkItDown(
                            llm_client=client,
                            llm_model=openai_model,
                            enable_plugins=True
                        )
                        if self.tool_base:
                            self.tool_base.log_info("MarkItDown initialized with LLM support")
                    except TypeError as e:
                        # Try alternative initialization approaches
                        if self.tool_base:
                            self.tool_base.log_warning(f"Direct LLM initialization failed: {e}")
                        
                        try:
                            self.markitdown = MarkItDown(
                                enable_plugins=True,
                                **{'llm_client': client, 'llm_model': openai_model}
                            )
                            if self.tool_base:
                                self.tool_base.log_info("MarkItDown initialized with LLM support (kwargs)")
                        except Exception as e:
                            if self.tool_base:
                                self.tool_base.log_warning(f"Kwargs initialization failed: {e}")
                            
                            # Fallback: create instance then set attributes
                            self.markitdown = MarkItDown(enable_plugins=True)
                            try:
                                self.markitdown.llm_client = client
                                self.markitdown.llm_model = openai_model
                                if self.tool_base:
                                    self.tool_base.log_info("LLM client set on MarkItDown instance")
                            except Exception as e:
                                try:
                                    self.markitdown._llm_client = client
                                    self.markitdown._llm_model = openai_model
                                    if self.tool_base:
                                        self.tool_base.log_info("LLM client set as private attributes")
                                except Exception as e:
                                    if self.tool_base:
                                        self.tool_base.log_warning(f"Failed to set LLM client: {e}")
                    
                    if self.tool_base:
                        self.tool_base.log_info(f"MarkItDown initialized with OpenAI model: {openai_model}")
                    
                except ImportError:
                    # OpenAI not available, fall back to basic MarkItDown
                    self.markitdown = MarkItDown(enable_plugins=False)
                    if self.tool_base and hasattr(self.tool_base, 'log_warning'):
                        self.tool_base.log_warning("OpenAI library not available, using basic MarkItDown")
                except Exception as e:
                    # Error with OpenAI setup, fall back to basic MarkItDown
                    self.markitdown = MarkItDown(enable_plugins=False)
                    if self.tool_base and hasattr(self.tool_base, 'log_warning'):
                        self.tool_base.log_warning(f"OpenAI setup failed, using basic MarkItDown: {e}")
            else:
                # Use basic MarkItDown without LLM features
                self.markitdown = MarkItDown(enable_plugins=False)
                
        except Exception as e:
            # Fallback to basic MarkItDown
            self.markitdown = MarkItDown(enable_plugins=False)
            if self.tool_base and hasattr(self.tool_base, 'log_error'):
                self.tool_base.log_error(f"Error initializing MarkItDown: {e}")
    
    async def _ocr_pdf_with_llm(self, pdf_content: bytes, llm_client) -> str:
        """Convert PDF pages to images and use LLM for OCR."""
        try:
            # Try to import required libraries for PDF to image conversion
            try:
                import fitz  # PyMuPDF
            except ImportError:
                self.tool_base.log_error("PyMuPDF not available - cannot convert PDF to images for OCR")
                return ""
            
            self.tool_base.log_info("Converting PDF pages to images for OCR...")
            
            # Open PDF with PyMuPDF
            pdf_doc = fitz.open(stream=pdf_content, filetype="pdf")
            extracted_text = []
            
            # Process first few pages (limit to avoid excessive API calls)
            max_pages = min(5, pdf_doc.page_count)
            self.tool_base.log_info(f"Processing {max_pages} pages out of {pdf_doc.page_count} total pages")
            
            for page_num in range(max_pages):
                page = pdf_doc[page_num]
                
                # Convert page to image
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better quality
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                
                # Convert to base64 for LLM
                import base64
                img_base64 = base64.b64encode(img_data).decode()
                
                self.tool_base.log_info(f"Processing page {page_num + 1} with LLM...")
                
                # Use LLM to extract text from image
                try:
                    response = llm_client.chat.completions.create(
                        model=getattr(self.markitdown, '_llm_model', 'gpt-4o'),
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Please extract all text from this image. Return only the text content, maintaining the original structure and formatting as much as possible. If there are multiple columns, preserve the reading order."
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{img_base64}"
                                        }
                                    }
                                ]
                            }
                        ],
                        max_tokens=4000
                    )
                    
                    page_text = response.choices[0].message.content
                    if page_text and page_text.strip():
                        extracted_text.append(f"# Page {page_num + 1}\n\n{page_text.strip()}\n\n")
                        self.tool_base.log_info(f"Extracted {len(page_text)} characters from page {page_num + 1}")
                    else:
                        self.tool_base.log_info(f"No text extracted from page {page_num + 1}")
                        
                except Exception as e:
                    self.tool_base.log_error(f"LLM OCR failed for page {page_num + 1}: {e}")
                    continue
            
            pdf_doc.close()
            
            # Combine all extracted text
            full_text = "\n".join(extracted_text)
            self.tool_base.log_info(f"OCR completed. Total extracted text length: {len(full_text)}")
            
            return full_text
            
        except Exception as e:
            self.tool_base.log_error(f"PDF OCR conversion failed: {e}")
            return ""
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="file_to_markdown",
            description="Convert various file formats (PDF, DOCX, images, etc.) to Markdown",
            endpoint="/convert",
            tool_type="converter",  # Direct string instead of enum
            version="1.0.0"
        )
    
    def get_schema(self) -> ToolSchema:
        return ToolSchema(
            properties={
                "file_content": {
                    "type": "string",
                    "format": "binary",
                    "description": "File content as bytes"
                },
                "filename": {
                    "type": "string",
                    "description": "Original filename with extension"
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type of the file"
                },
                "base64_content": {
                    "type": "string",
                    "description": "Base64 encoded file content (alternative to file_content)"
                }
            },
            required=["filename"]
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Convert file to markdown."""
        # Debug incoming parameters
        if self.tool_base:
            self.tool_base.log_info(f"Execute called with parameters: {list(kwargs.keys())}")
            self.tool_base.log_info(f"Filename: {kwargs.get('filename')}")
            self.tool_base.log_info(f"Has file_content: {bool(kwargs.get('file_content'))}")
            self.tool_base.log_info(f"Has base64_content: {bool(kwargs.get('base64_content'))}")
            if kwargs.get('base64_content'):
                self.tool_base.log_info(f"Base64 content length: {len(kwargs.get('base64_content'))}")
        
        if not self.markitdown:
            return {
                "success": False,
                "error": "MarkItDown library not available"
            }
        
        filename = kwargs.get("filename")
        if not filename:
            return {
                "success": False,
                "error": "filename is required"
            }
          # Get file content
        file_content = kwargs.get("file_content")
        base64_content = kwargs.get("base64_content")
        
        if base64_content:
            try:
                # Clean up base64 content - remove whitespace and fix padding if needed
                base64_clean = base64_content.strip().replace('\n', '').replace('\r', '').replace(' ', '')
                
                # Fix padding if necessary
                missing_padding = len(base64_clean) % 4
                if missing_padding:
                    base64_clean += '=' * (4 - missing_padding)
                
                if self.tool_base:
                    self.tool_base.log_info(f"Original base64 length: {len(base64_content)}")
                    self.tool_base.log_info(f"Cleaned base64 length: {len(base64_clean)}")
                    self.tool_base.log_info(f"Base64 starts with: {base64_clean[:20]}...")
                    self.tool_base.log_info(f"Base64 ends with: ...{base64_clean[-20:]}")
                
                file_content = base64.b64decode(base64_clean)
                
                # Debug the decoded content
                if self.tool_base:
                    self.tool_base.log_info(f"Base64 decoded successfully, content length: {len(file_content)}")
                    # Check if it looks like a PDF by examining the header
                    if len(file_content) >= 8:
                        header = file_content[:8]
                        self.tool_base.log_info(f"File header bytes: {header}")
                        self.tool_base.log_info(f"File header as string: {header.decode('latin-1', errors='ignore')}")
                        # PDF files should start with %PDF
                        if header.startswith(b'%PDF'):
                            self.tool_base.log_info("File appears to be a valid PDF based on header")
                        else:
                            self.tool_base.log_warning("File does not appear to be a PDF - missing %PDF header")
            except Exception as e:
                if self.tool_base:
                    self.tool_base.log_error(f"Base64 decode error: {str(e)}")
                return {
                    "success": False,
                    "error": f"Invalid base64 content: {str(e)}"
                }
        
        if not file_content:
            return {
                "success": False,
                "error": "No file content provided"
            }
        
        try:
            # Create a BytesIO object from the content
            if isinstance(file_content, str):
                file_content = file_content.encode('utf-8')
            
            file_stream = io.BytesIO(file_content)
              # Debug file stream
            if self.tool_base:
                self.tool_base.log_info(f"File stream created, size: {len(file_content)} bytes")
                self.tool_base.log_info(f"File extension: {Path(filename).suffix.lower()}")
            
            # Convert using MarkItDown
            if self.tool_base:
                self.tool_base.log_info("Starting MarkItDown conversion...")
                self.tool_base.log_info(f"MarkItDown instance: {self.markitdown}")
                
                # Check for LLM client in multiple possible locations
                llm_client_found = False
                llm_client = None
                
                if hasattr(self.markitdown, 'llm_client') and self.markitdown.llm_client:
                    llm_client = self.markitdown.llm_client
                    llm_client_found = True
                    self.tool_base.log_info(f"LLM client found at llm_client: {llm_client}")
                elif hasattr(self.markitdown, '_llm_client') and self.markitdown._llm_client:
                    llm_client = self.markitdown._llm_client
                    llm_client_found = True
                    self.tool_base.log_info(f"LLM client found at _llm_client: {llm_client}")
                else:
                    self.tool_base.log_info("No LLM client found")
                
                self.tool_base.log_info(f"LLM client available: {llm_client_found}")
                
                # Check what converters are available and which one will be used
                try:
                    converters = getattr(self.markitdown, '_converters', [])
                    if isinstance(converters, list):
                        converter_names = []
                        for conv in converters:
                            try:
                                # Try to get converter name/type
                                conv_name = str(type(conv).__name__)
                                converter_names.append(conv_name)
                            except:
                                converter_names.append(str(conv))
                        self.tool_base.log_info(f"Available converters: {converter_names}")
                        
                        # Try to determine which converter will be used for this file
                        file_ext = Path(filename).suffix.lower()
                        self.tool_base.log_info(f"File extension: {file_ext}")
                        
                        # Look for PDF-specific converter
                        pdf_converters = [conv for conv in converter_names if 'pdf' in conv.lower() or 'Pdf' in conv]
                        if pdf_converters:
                            self.tool_base.log_info(f"PDF converters found: {pdf_converters}")
                        else:
                            self.tool_base.log_info("No obvious PDF converters found")
                            
                    elif isinstance(converters, dict):
                        self.tool_base.log_info(f"Available converters (dict): {list(converters.keys())}")
                    else:
                        self.tool_base.log_info(f"Converters type: {type(converters)}, value: {converters}")
                except Exception as e:
                    self.tool_base.log_info(f"Could not get converters: {e}")
                
                # Also check for other converter-related attributes
                try:
                    for attr in ['converters', 'converter_registry', '_converter_registry']:
                        if hasattr(self.markitdown, attr):
                            attr_value = getattr(self.markitdown, attr)
                            self.tool_base.log_info(f"MarkItDown.{attr}: {type(attr_value)} = {attr_value}")
                except Exception as e:
                    self.tool_base.log_info(f"Error checking converter attributes: {e}")
            
            result = self.markitdown.convert_stream(
                file_stream,
                file_extension=Path(filename).suffix.lower(),
                filename=filename
            )
            
            if self.tool_base:
                self.tool_base.log_info("MarkItDown conversion completed")
                
                # Debug the result more thoroughly
                self.tool_base.log_info(f"Result type: {type(result)}")
                self.tool_base.log_info(f"Result dir: {[attr for attr in dir(result) if not attr.startswith('__')]}")
                
                # Check the markdown content
                if hasattr(result, 'markdown'):
                    markdown_content = result.markdown
                    self.tool_base.log_info(f"Markdown content length: {len(markdown_content) if markdown_content else 0}")
                    if markdown_content and len(markdown_content.strip()) > 0:
                        self.tool_base.log_info(f"Markdown preview: {markdown_content[:300]}...")
                    else:
                        self.tool_base.log_info("Markdown is empty or whitespace only")
                else:
                    self.tool_base.log_info("Result has no 'markdown' attribute")
                
                # Check text_content if available
                if hasattr(result, 'text_content'):
                    text_content = result.text_content
                    self.tool_base.log_info(f"Text content length: {len(text_content) if text_content else 0}")
                    if text_content and len(text_content.strip()) > 0:
                        self.tool_base.log_info(f"Text content preview: {text_content[:300]}...")
                    else:
                        self.tool_base.log_info("Text content is empty or whitespace only")
                else:
                    self.tool_base.log_info("Result has no 'text_content' attribute")
                  # For PDFs specifically, check if this might be a scanned/image-based PDF
                if Path(filename).suffix.lower() == '.pdf':
                    self.tool_base.log_info("Processing PDF file - checking for text extraction issues")
                    
                    # If we got empty results from a PDF, it might be scanned
                    if (not hasattr(result, 'markdown') or not result.markdown or len(result.markdown.strip()) == 0):
                        self.tool_base.log_info("PDF returned empty markdown - this might be a scanned/image-based PDF")
                        self.tool_base.log_info("MarkItDown's PDF converter may not handle scanned documents well")
                        
                        # For now, just log this. In a future version, we could add OCR here
                        # by converting PDF pages to images and using the LLM on those images
                        if llm_client_found and llm_client:
                            self.tool_base.log_info(f"LLM client is available ({type(llm_client)}) - could potentially add OCR for scanned PDFs")
                            self.tool_base.log_info("This scanned PDF could benefit from OCR processing using the LLM")
                            
                            # Try to implement OCR fallback for scanned PDFs
                            try:
                                self.tool_base.log_info("Attempting OCR fallback for scanned PDF...")
                                ocr_result = await self._ocr_pdf_with_llm(file_content, llm_client)
                                if ocr_result and ocr_result.strip():
                                    self.tool_base.log_info(f"OCR successful! Extracted {len(ocr_result)} characters")
                                    # Override the empty result with OCR content
                                    result.markdown = ocr_result
                                    result.text_content = ocr_result
                                    self.tool_base.log_info(f"OCR result preview: {ocr_result[:200]}...")
                                else:
                                    self.tool_base.log_info("OCR did not extract any content")
                            except Exception as e:
                                self.tool_base.log_error(f"OCR fallback failed: {e}")
                        else:
                            self.tool_base.log_info("No LLM client available for OCR enhancement")
            
            # Debug logging
            if self.tool_base:
                self.tool_base.log_info(f"MarkItDown result type: {type(result)}")
                self.tool_base.log_info(f"MarkItDown result attributes: {dir(result)}")
                self.tool_base.log_info(f"MarkItDown markdown length: {len(result.markdown) if hasattr(result, 'markdown') and result.markdown else 0}")
                self.tool_base.log_info(f"MarkItDown title: {result.title if hasattr(result, 'title') else 'No title'}")
                if hasattr(result, 'markdown') and result.markdown:
                    self.tool_base.log_info(f"MarkItDown markdown preview (first 200 chars): {result.markdown[:200]}")
                else:
                    self.tool_base.log_info("MarkItDown markdown is empty or None")
            
            # Log success
            if self.tool_base:
                self.tool_base.log_info(f"Successfully converted {filename} to markdown")
                self.tool_base.record_metric("conversion.success", 1)
            
            # Create response
            response = {
                "success": True,
                "markdown": result.markdown,
                "title": result.title,
                "filename": filename,
                "content_type": kwargs.get("content_type"),
                "size": len(file_content)
            }
            
            # Debug the response
            if self.tool_base:
                self.tool_base.log_info(f"Response keys: {list(response.keys())}")
                self.tool_base.log_info(f"Response markdown length: {len(response.get('markdown', ''))}")
                self.tool_base.log_info(f"Response structure: success={response['success']}, title='{response['title']}', size={response['size']}")
            
            return response
            
        except Exception as e:
            error_msg = f"Conversion failed: {str(e)}"
            if self.tool_base:
                self.tool_base.log_error(error_msg)
                self.tool_base.record_metric("conversion.error", 1)
            
            return {
                "success": False,
                "error": error_msg,
                "filename": filename
            }


def setup_tool(tool_base):
    """Setup function called by the core service."""
    return FileToMarkdownConverter(tool_base)
