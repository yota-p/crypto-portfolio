# To use: echo export_pythonpath.sh
#!/bin/bash

export PYTHONPATH=/home/ec2-user/git/crypto-portfolio/src
echo $PYTHONPATH

# don't create pycache
export PYTHONDONTWRITEBYTECODE=1
