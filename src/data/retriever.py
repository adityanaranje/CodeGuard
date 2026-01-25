import os
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import Optional

def get_code_retriever(repo_path: str):
    """
    Creates or loads a retriever for the repository.
    
    Args:
        repo_path: Path to the repository root.
        
    Returns:
        A LangChain retriever object.
    """
    print(f"--- Initializing Retriever for {repo_path} ---")
    
    # 1. Load Documents
    loader = DirectoryLoader(
        repo_path,
        glob="**/*.py", # Focus on Python files for this Py app
        loader_cls=TextLoader,
        use_multithreading=True,
        silent_errors=True
    )
    
    try:
        raw_docs = loader.load()
    except Exception as e:
        print(f"Error loading docs: {e}")
        return None

    if not raw_docs:
        print("Warning: No documents found.")
        return None

    # Filter out noise (venv, .git, etc)
    ignore_substrings = ["/venv/", "\\venv\\", "/.venv/", "\\.venv\\", "site-packages", "__pycache__", "/.git/"]
    docs = []
    for doc in raw_docs:
        source = doc.metadata.get("source", "")
        if not any(sub in source for sub in ignore_substrings):
             docs.append(doc)
    
    if not docs:
         print("Warning: All docs filtered out (maybe only venv files found?)")
         return None

    # 2. Split Documents
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,
        chunk_overlap=200
    )
    splits = text_splitter.split_documents(docs)
    
    # 3. Vector Store - Moving heavy imports here to save global memory
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    
    # Using HuggingFaceEmbeddings as a robust, free default
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    
    # Create ephemeral vector store for this session/repo
    vectorstore = Chroma.from_documents(
        documents=splits, 
        embedding=embeddings,
        collection_name="repo_code_context"
    )
    
    # 4. Return as Retriever
    return vectorstore.as_retriever(
        search_type="mmr",
        search_kwargs={"k": 5}
    )
