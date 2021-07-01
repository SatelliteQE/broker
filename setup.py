#!/usr/bin/env python
from setuptools import setup, find_packages

with open("README.md") as readme_file:
    readme = readme_file.read()

with open("HISTORY.md") as history_file:
    history = history_file.read()

requirements = ["awxkit", "click", "dynaconf>=3.1.0", "logzero", "pyyaml", "ssh2-python"]

test_requirements = ['pytest']

setup_requirements = ['setuptools', 'wheel']

extras = {
    'test': test_requirements,
    'setup': setup_requirements,
}

setup(
    name="broker",
    version="0.1.21",
    description="The infrastructure middleman.",
    long_description=readme + "\n\n" + history,
    long_description_content_type="text/markdown",
    author="Jacob J Callahan",
    author_email="jacob.callahan05@gmail.com",
    url="https://github.com/SatelliteQE/broker",
    packages=find_packages(),
    entry_points={"console_scripts": ["broker=broker.commands:cli"]},
    include_package_data=True,
    install_requires=requirements,
    tests_require=test_requirements,
    extras_require=extras,
    setup_requires=setup_requirements,
    license="GNU General Public License v3",
    zip_safe=False,
    keywords="broker",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
)
