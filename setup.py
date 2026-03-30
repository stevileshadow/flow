from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="flow",
    version="0.0.1",
    description="Field Service Management app for Frappe/ERPNext",
    author="stevileshadow",
    author_email="ton-courriel@exemple.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
