from setuptools import setup, find_packages

setup(
    name="agentcast-sdk",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "cryptography>=42.0.0",
        "httpx>=0.27.0",
    ],
)
