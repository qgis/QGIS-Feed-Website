# Python packages that nixpkgs does not provide, or provides at a version this
# project cannot use.
#
# These are real derivations rather than a pip install step so that the
# production closure is hermetic and reproducible: `nix build` has no network
# access, so anything fetched by pip at build time would not work here.
#
# None of these set pythonImportsCheck. They are Django applications, and
# importing one outside a configured project raises ImproperlyConfigured as
# soon as it touches settings - django-imagekit reads IMAGEKIT_* through
# django-appconf at import time, for example. They are exercised for real by
# the project's own test suite instead.
#
# Keep the versions in step with REQUIREMENTS.txt.
{ python, lib }:

let
  ps = python.pkgs;
in
{
  # Not in nixpkgs.
  django-imagekit = ps.buildPythonPackage rec {
    pname = "django-imagekit";
    version = "5.0.0";
    pyproject = true;

    src = ps.fetchPypi {
      inherit pname version;
      hash = "sha256-qun3So6bbOtdFffY4mYwKQHnbZ9TLHi9UTXLD6IGprA=";
    };

    build-system = [ ps.setuptools ];

    dependencies = [
      ps.django
      ps.pilkit
      ps.django-appconf
    ];

    # Upstream tests need a live Django settings module and a database.
    doCheck = false;

    meta = {
      description = "Automated image processing for Django models";
      homepage = "https://github.com/matthewwithanm/django-imagekit";
      license = lib.licenses.bsd3;
    };
  };

  # Not in nixpkgs. Held at 2.2 deliberately: 2.3 turned UserVisitMiddleware
  # from a class into a function, and qgisfeed/middleware.py subclasses it.
  django-user-visit = ps.buildPythonPackage rec {
    pname = "django-user-visit";
    version = "2.2";

    # Built from the wheel rather than the sdist. The 2.2 sdist declares the
    # legacy "poetry.masonry.api" build backend, which poetry-core does not
    # provide - only the full poetry package does. The wheel needs no build
    # backend at all, so this avoids pulling poetry into the closure.
    format = "wheel";

    src = ps.fetchPypi {
      pname = "django_user_visit";
      inherit version format;
      dist = "py3";
      python = "py3";
      hash = "sha256-BO/k00cXn0dpgZjYNhQdY5FOXaYyG6ZqckJPhSKAgf8=";
    };

    dependencies = [
      ps.django
      ps.user-agents
    ];

    doCheck = false;

    meta = {
      description = "Django app used to track user visits";
      homepage = "https://github.com/yunojuno/django-user-visit";
      license = lib.licenses.mit;
    };
  };

  # nixpkgs carries 3.2.x, which requires webpack-bundle-tracker 3.x and a
  # different stats-file format than package.json produces.
  django-webpack-loader = ps.buildPythonPackage rec {
    pname = "django-webpack-loader";
    version = "2.0.1";
    pyproject = true;

    src = ps.fetchPypi {
      inherit pname version;
      hash = "sha256-Do37L82znb/QG+dgPAYBMqRmT0g4Ec48dfLTwNOat2I=";
    };

    build-system = [ ps.setuptools ];

    dependencies = [ ps.django ];

    doCheck = false;

    meta = {
      description = "Transparently use webpack with Django";
      homepage = "https://github.com/django-webpack/django-webpack-loader";
      license = lib.licenses.mit;
    };
  };
}
