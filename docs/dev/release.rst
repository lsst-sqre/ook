#################
Release procedure
#################

This page gives an overview of how Ook releases are made.
This information is only useful for maintainers.

Ook's releases are largely automated through GitHub Actions (see the `ci.yaml`_ workflow file for details).
Ook Docker images are published on `GitHub Container Registry <https://github.com/orgs/lsst-sqre/packages?repo_name=ook>`__.

.. _`ci.yaml`: https://github.com/lsst-sqre/squarebot/blob/main/.github/workflows/ci.yaml

.. _regular-release:

Regular releases
================

Regular releases happen from the ``main`` branch after changes have been merged.
From the ``main`` branch you can release a new major version (``X.0.0``), a new minor version of the current major version (``X.Y.0``), or a new patch of the current major-minor version (``X.Y.Z``).
See :ref:`backport-release` to patch an earlier major-minor version.

Release tags are semantic version identifiers following the :pep:`440` specification.

1. Change log and documentation
-------------------------------

Change log messages for each release are accumulated using scriv_ (see :ref:`dev-change-log`).
When it comes time to make the release, there should be a collection of change log fragments in :file:`changelog.d`.
Those fragments will make up the change log for the new release.

Review those fragments to determine the version number of the next release.
Ook follows semver_, so follow its rules to pick the next version:

- If there are any backward-incompatible changes, increment the major version number and set the other numbers to 0.
- If there are any new features, increment the minor version number and set the patch version to 0.
- Otherwise, increment the patch version number.

Run the nox scriv-collect session with the version number you decided on:

.. tab-set:::

   .. tab-item:: In venv

      .. code-block:: sh

         nox -s scriv-collect X.Y.Z

   .. tab-item:: Without pre-installation

      .. code-block:: sh

         uv run --group=nox nox -s scriv-collect X.Y.Z

This will delete the fragment files and collect them into :file:`CHANGELOG.md` under an entry for the new release.
Review that entry and edit it as needed (proofread, change the order to put more important things first, etc.).

Finally, create a PR from those changes and merge it before continuing with the release process.

2. Create a GitHub release
--------------------------

Add a new GitHub release for this version:

- The release title should be the same as the version number.
- The release tag should be the version: ``X.Y.Z``.

  .. note::

     The tag **must** follow the :pep:`440` specification since Ook uses setuptools_scm_ to set version metadata based on Git tags.
     In particular, **don't** prefix the tag with ``v``.

.. _setuptools_scm: https://github.com/pypa/setuptools_scm

Start the description of the release by using the :guilabel:`Generate release notes` button to include the GitHub-generated summary of pull requests.
Then, above that, paste the contents of the :file:`CHANGELOG.md` entry for this release, without the initial heading specifying the version number and date.
Adjust the heading depth of the subsections to use ``##`` instead of ``###`` to match the pull request summary.

Once the release is created, `ci.yaml`_ GitHub Actions workflow uploads the new release to Docker Hub.

.. _backport-release:

Backport releases
=================

The regular release procedure works from the main line of development on the ``main`` Git branch.
To create a release that patches an earlier major or minor version, you need to release from a **release branch.**

Creating a release branch
-------------------------

Release branches are named after the major and minor components of the version string: ``X.Y``.
If the release branch doesn't already exist, check out the latest patch for that major-minor version:

.. code-block:: sh

   git checkout X.Y.Z
   git checkout -b X.Y
   git push -u

Developing on a release branch
------------------------------

Once a release branch exists, it becomes the "master" branch for patches of that major-minor version.
Pull requests should be based on, and merged into, the release branch.

If the development on the release branch is a backport of commits on the ``main`` branch, use ``git cherry-pick`` to copy those commits into a new pull request against the release branch.

Releasing from a release branch
-------------------------------

Releases from a release branch are equivalent to :ref:`regular releases <regular-release>`, except that the release branch takes the role of the ``main`` branch.
