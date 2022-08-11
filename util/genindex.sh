#!/bin/bash

# Simple utility to generate the repository indexes for
# R5 and pre-R5 OBRs.
#
# (C) Copyright 2012 - The Amdatu Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Changelog:
#
# v1.0 | December 12, 2012 | jawi | initial version
# v1.1 | April 20, 2013 | bramk | script requires bash
#                                 included dependencies repo in -a
#                                 generate index.xml
# v1.1 | November 7, 2014 | mauricer | Updated from bindex to repoindex.cli-2.1.4 and added sha calculations
                          

# Sanity check, only allow this script to be run from root directory
if [[ ! -d "release" || ! -d "snapshot" || ! -d "dependencies" ]]; then
    echo "${0##*/} should be run in the root of amdatu-repository!"
    exit 1
fi

# Determine what we want to index...
dirs=""
if [[ "$1" == "-a" ]]; then
    dirs=( release snapshot dependencies )
elif [[ -d "$1" ]]; then
    dirs=($1)
else
    if [[ "$1" != "" ]]; then
        echo "No such directory: $1\n"
    fi
    echo "Usage: ${0##*/} [-a]|<directory>"
    exit 1
fi

# Locate a valid Java JRE
java=""
if [[ -z "$JAVA_HOME" ]]; then
    # TODO this will break on non-OSX machines?!
    java=/usr/bin/java
else
    java=$JAVA_HOME/bin/java
fi
if [[ ! -f "$java" ]]; then
    echo "Unable to locate a valid java binary at $java!"
fi

bindex="util/lib/bindex.jar"
repoindex="util/lib/org.osgi.impl.bundle.repoindex.cli-2.1.4.jar"

# Generate the actual index files...
for dir in ${dirs[@]}; do
    echo "Processing $dir ..."

    # find all JAR-files except for those ending with -sources.jar!
    files=`find $dir -name '*.jar' ! -name '*-sources.jar' -print`
    
    if [[ -f $repoindex ]]; then
      echo "Running $repoindex ..."
      # Generate index.xml.gz for dir
      $java -jar $repoindex $dir -d $dir -r $dir/index.xml.gz
      # Extract index.xml.gz to index.xml
      #gzcat $dir/index.xml.gz > $dir/index.xml
      gunzip  -c $dir/index.xml.gz > $dir/index.xml
      # Calculate sha's for index.xml.gz and index.xml
      sha256sum $dir/index.xml.gz | cut -f 1 -d " " > $dir/index.xml.gz.sha
      sha256sum $dir/index.xml | cut -f 1 -d " " > $dir/index.xml.sha
    fi

    if [[ -f $bindex ]]; then
      echo "Running $bindex ..."

      $java -jar $bindex -q -d $dir -r $dir/repository.xml -n "Amdatu ${dir}s" $files
    fi
done

###EOF
