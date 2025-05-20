#################
Development guide
#################

This page provides procedures and guidelines for developing and contributing to Ook.

Scope of contributions
======================

Ook is an open source package, meaning that you can contribute to Ook itself, or fork Squarebot for your own purposes.

Since Ook is intended for internal use by Rubin Observatory, community contributions can only be accepted if they align with Rubin Observatory's aims.
For that reason, it's a good idea to propose changes with a new `GitHub issue`_ before investing time in making a pull request.

Ook is developed by the Rubin Observatory SQuaRE team.

.. _GitHub issue: https://github.com/lsst-sqre/ook/issues/new

.. _dev-environment:

Setting up a local development environment
==========================================

Local development requires:

- Python 3.13
- Uv_
- Docker or a compatible container runtime

First, clone the Ook repository:

.. code-block:: sh

   git clone https://github.com/lsst-sqre/ook.git
   cd ook

Ook is a Python project that should be developed within a virtual environment.
Create this virtual environment with UV:

.. code-block:: sh

   uv venv
   source .venv/bin/activate

Then install the development dependencies and pre-commit hooks:

.. code-block:: sh

   make init

.. _pre-commit-hooks:

Pre-commit hooks
================

The pre-commit hooks ensure that files are valid and properly formatted.
The hooks are automatically installed by running the :command:`make init`.

When these hooks fail, your Git commit will be aborted.
To proceed, stage the new modifications and proceed with your Git commit.

Refer to the :file:`.pre-commit-config.yaml` file for the list of hooks that are run.

.. _dev-run-tests:

Running tests
=============

To test all components of Ook, run nox_, which tests the library the same way that the GitHub Actions CI workflow does.

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox

File linting, type checking, and unit tests are run as separate nox sessions:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s lint
         nox -s typing
         nox -s unit

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s lint
         uv run --group=nox nox -s typing
         uv run --group=nox nox -s unit

With unit tests, you can run a specific test file or directory:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s test -- tests/foo_test.py

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s test -- tests/foo_test.py

To see a listing of specific nox sessions, run:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox --list

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox --list


Building documentation
======================

Documentation is built with Sphinx_:

.. _Sphinx: https://www.sphinx-doc.org/en/master/


.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s docs

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s docs

The built documentation is located in the :file:`docs/_build/html` directory.

To check the documentation for broken links, run:

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s docs-linkcheck

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s docs-linkcheck

.. _dev-change-log:

Updating the change log
=======================

Ook uses scriv_ to maintain its change log.

When preparing a pull request, run

.. tab-set::

   .. tab-item:: In venv
      :sync: venv

      .. code-block:: sh

         nox -s scriv-create

   .. tab-item:: Without pre-installation
      :sync: uv

      .. code-block:: sh

         uv run --group=nox nox -s scriv-create

This will create a change log fragment in :file:`changelog.d`.
Edit that fragment, removing the sections that do not apply and adding entries for your pull request.

Change log entries use the following sections:

- **Backward-incompatible changes**
- **New features**
- **Bug fixes**
- **Other changes** (for minor, patch-level changes that are not bug fixes, such as logging formatting changes or updates to the documentation)

Do not include a change log entry solely for updating pinned dependencies, without any visible change to Ook's behavior.
Every release is implicitly assumed to update all pinned dependencies.

These entries will eventually be cut and pasted into the release description for the next release, so the Markdown for the change descriptions must be compatible with GitHub's Markdown conventions for the release description.
Specifically:

- Each bullet point should be entirely on one line, even if it contains multiple sentences.
  This is an exception to the normal documentation convention of a newline after each sentence.
  Unfortunately, GitHub interprets those newlines as hard line breaks, so they would result in an ugly release description.
- Avoid using too much complex markup, such as nested bullet lists, since the formatting in the GitHub release description may not be what you expect and manually editing it is tedious.

.. _style-guide:

Style guide
===========

Code
----

- The code style follows :pep:`8`, though in practice lean on Black and ruff to format the code for you. Use :sqr:`072` for for architectural guidance.

- Use :pep:`484` type annotations.
  The ``nox -s typing`` test session, which runs mypy_, ensures that the project's types are consistent.

- Write tests for Pytest_.

Documentation
-------------

- Follow the `LSST DM User Documentation Style Guide`_, which is primarily based on the `Google Developer Style Guide`_.

- Document the Python API with numpydoc-formatted docstrings.
  See the `LSST DM Docstring Style Guide`_.

- Follow the `LSST DM ReStructuredTextStyle Guide`_.
  In particular, ensure that prose is written **one-sentence-per-line** for better Git diffs.

.. _`LSST DM User Documentation Style Guide`: https://developer.lsst.io/user-docs/index.html
.. _`Google Developer Style Guide`: https://developers.google.com/style/
.. _`LSST DM Docstring Style Guide`: https://developer.lsst.io/python/style.html
.. _`LSST DM ReStructuredTextStyle Guide`: https://developer.lsst.io/restructuredtext/style.html
