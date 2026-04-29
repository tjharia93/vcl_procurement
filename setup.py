from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="vcl_procurement",
    version="1.0.0",
    description="VCL Procurement App",
    author="VCL",
    author_email="info@vcl.co.tz",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
