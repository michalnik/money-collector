# money-collector
Money collector - use it to collect a lot of money from you clients :-).

# install
## to maintain on localhost
It'll install project in development mode into virtual environment.
Prerequisite here is `pyenv`, please be aware of that.

```bash
git clone https://github.com/michalnik/money-collector.git
cd money-collector
make install
```
## to run from virtual environment
```bash
pyenv install -s 3.13
pyenv virtualenv 3.13 money-collector-3.13
pyenv activate money-collector-3.13

pip install git+https://github.com/michalnik/money-collector.git@main
```

# run
## in development mode
```bash
make run
```
## just for money
```bash
collector
```
