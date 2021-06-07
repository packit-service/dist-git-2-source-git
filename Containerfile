# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

ARG base_image=quay.io/centos/centos:8
ARG package_manager
FROM $base_image

# GIT_EDITOR: git cherry-pick --continue prompts editor, this is the only way to make it non-interactive
ENV LANG=en_US.UTF-8 \
    ANSIBLE_PYTHON_INTERPRETER=/usr/bin/python3 \
    ANSIBLE_STDOUT_CALLBACK=debug \
    USER=packit \
    HOME=/home/packit \
    package_manager=${package_manager:-"dnf -y"} \
    GIT_EDITOR=true

RUN curl --output /usr/bin/get_sources.sh https://git.centos.org/centos-git-common/raw/master/f/get_sources.sh && chmod +x /usr/bin/get_sources.sh

COPY macros.packit /usr/lib/rpm/macros.d/macros.packit
COPY packitpatch /usr/bin/packitpatch

COPY files/install-deps.yml files/install-deps-worker.yml /src/
RUN $package_manager install epel-release \
    && $package_manager install ansible python3-pip dnf-plugins-core \
    && ansible-playbook -vv -c local -i localhost, /src/install-deps.yml \
    && ansible-playbook -vv -c local -i localhost, /src/install-deps-worker.yml \
    && $package_manager clean all

COPY README.md setup.cfg setup.py files/gitconfig files/run_worker.sh files/setup_env_in_openshift.sh /src/
COPY dist2src /src/dist2src
RUN ln -s -f /src/pyproject.toml /pyproject.toml
# setuptools-scm
COPY .git /src/.git

WORKDIR /src
RUN pip3 install git+https://github.com/packit/requre.git
RUN pip3 install . && pip3 check

WORKDIR /workdir
VOLUME /workdir

# Celery worker which runs tasks from centosmsg listener
COPY files/recipe-worker.yml /src/
RUN ansible-playbook -vv -c local -i localhost, /src/recipe-worker.yml

CMD ["dist2src"]
