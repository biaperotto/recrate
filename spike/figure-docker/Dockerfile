# THROWAWAY SPIKE — pinned to Jones et al. (2026), not general reCRATE code.
#
# R pinned to the version the paper's own session info records (R 4.4.2), with
# the packages its plotting code needs. rocker/r-ver pins a Posit Package
# Manager snapshot matching the R release, so package versions are reproducible
# without naming each one.
#
# Deliberately NOT installed: brms / rstan / cmdstanr. The swimmer plot is built
# from the authors' shared spreadsheet and needs no model fit, so the image
# stays small and builds in minutes rather than hours of Stan C++ compilation.

FROM rocker/r-ver:4.4.2

RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2-dev libfontconfig1-dev libfreetype6-dev libpng-dev \
        libtiff5-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

RUN install2.r --error --skipinstalled \
        readxl dplyr ggplot2 forcats

WORKDIR /work
COPY swimmer.R /work/swimmer.R

ENTRYPOINT ["Rscript", "/work/swimmer.R"]
CMD ["/repo", "/out"]
