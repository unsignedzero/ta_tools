##!/usr/bin/env python
# -*- coding: utf-8 -*-
from collections import defaultdict
import datetime
import json
import os
import re
import subprocess
import platform

import logging
logger = logging.getLogger(__name__)

class Submissions(object):


    def __init__(self):
        self.folder_prefix = "6300Fall17"
        self.git_context = "gt-omscs-se-2017fall"
        self.student_records_filename = "student_records.json"
        self.student_alias_filename = "student_aliases.json"
        self.team_records_filename = "student_records_teams.json"
        self.team_members_filename = "student_records_team_members.json"
        self.datetime_format = "%Y-%m-%d %H:%M:%S"
        self.pull_from_github = True
        self._dict_cache = {}  # cache some dictionary info here to save on IO operations
        self._pulled_teams = []  # don't pull team repos up to 4x if you can avoid it

        self.target_repo_name = 'Repos'


    def create_student_json(self, input_file_name):

        try:
            with open(input_file_name, 'r') as input_file:

                gt_id_dict, student_dict = {}, {}

                for line in input_file:

                    line = line.strip()
                    parsed_line = line.split('\t')

                    name, gt_id, t_square_id = parsed_line[0:3]

                    student_dict[t_square_id] = {'name': name, 'gt_id': gt_id}
                    gt_id_dict[gt_id] = t_square_id

            with open(self.student_records_filename, 'w') as output_file:
                json.dump(student_dict, output_file)
            with open(self.student_alias_filename, 'w') as alias_file:
                json.dump(gt_id_dict, alias_file)

        except IOError:
            raise IOError(
              "create_student_json: couldn't find file with name %s. Exiting."
              % input_file_name)


    def create_team_json(self, input_file_name):

        try:

            with open(input_file_name, 'r') as input_file:

                student_dict, teams_dict = {}, defaultdict(list)

                for line in input_file:
                    parsed = line.strip().split('\t')

                    student = parsed[0]
                    team = parsed[2] if len(parsed) >= 3 else "None"

                    student_dict[student] = team
                    teams_dict[team].append(student)

            with open(self.team_records_filename, 'w') as student_teams_file:
                json.dump(student_dict, student_teams_file)
            with open(self.team_members_filename, 'w') as team_members_file:
                json.dump(teams_dict, team_members_file)

        except IOError:
            raise IOError(
              "create_team_json couldn't find file with name %s" %
              input_file_name)


    def prep_repos(self, submission_folder_name, deadline,
                   whitelist=None, is_team_project=False):

        assignment_alias = self.get_assignment_alias(submission_folder_name)

        if not os.path.isdir(self.target_repo_name):
            os.makedirs(self.target_repo_name)

        if not os.path.isdir(submission_folder_name):
            raise IOError("Submission folder name '%s' not found. Exiting." %
                          submission_folder_name)

        if is_team_project:
            teams = self.get_dictionary_from_json_file(
              self.team_records_filename)

        try:
            students = None

            with open(self.student_records_filename, 'r+') as (
              student_records_file):

                students = json.load(student_records_file)

                if whitelist is None:
                    folders = os.listdir(submission_folder_name)
                else:
                    folders = self.get_student_folder_names_from_list(
                      whitelist, is_team_project)

                for folder in folders:

                    # Check for hidden .DS_Store file in MacOS
                    if str(folder) == ".DS_Store":
                        continue

                    parsed = folder.split('(')
                    t_square_id = parsed[1].strip(')')

                    current_student = students.get(t_square_id, None)
                    current_student_id = current_student['gt_id']
                    if current_student is None:
                        continue

                    if (whitelist is not None and (
                      (not is_team_project and
                       current_student_id not in whitelist) or
                      (is_team_project and
                       teams[current_student_id] not in whitelist)
                      )):
                        continue

                    # reset info for current assignment
                    current_student[assignment_alias] = {}

                    # get submission text
                    current_student = self.check_submission_file(current_student, t_square_id, submission_folder_name, folder, assignment_alias)

                    # get t-square timestamp
                    current_student = self.check_timestamp_file(current_student, submission_folder_name, folder, assignment_alias)

                    # clone repo if needed - note that you'll need to authenticate with github here; debugger may not work properly
                    self.setup_student_repo(current_student, is_team_project)

                    # only check commit ID validity and GitHub timestamp on valid commits
                    if self.commit_id_present(current_student[assignment_alias]['commitID']):
                        # try to check out commit ID
                        current_student = self.check_commit_ID(current_student, assignment_alias, is_team_project)

                        current_student = self.check_timestamp_github(current_student, assignment_alias, deadline, is_team_project)

                    # check T-Square timestamp against deadline
                    current_student = self.check_timestamp_t_square(current_student, assignment_alias, deadline)

                    # save info
                    students[t_square_id] = current_student

            if students is not None:
                # save info
                with open(self.student_records_filename, 'w') as output_file:
                    json.dump(students, output_file)

            # check out most recent commit
            if is_team_project and whitelist is not None:
                teams = self.get_dictionary_from_json_file(self.team_members_filename)
                aliases = self.get_dictionary_from_json_file(self.student_alias_filename)

                for team in whitelist:
                    members, commits = teams[team], []

                    for student in members:
                        t_square_id = aliases[student]
                        student_info = students[t_square_id]

                        try:
                            commit_time = student_info[assignment_alias]['Timestamp GitHub']
                            commit_ID = student_info[assignment_alias]['commitID']
                        except KeyError:
                            continue

                        if self.commit_id_present(commit_ID) and commit_time != 'N/A':
                            commits.append((commit_time, commit_ID))

                    commits.sort(reverse=True) # most recent should be first

                    try:
                        most_recent_commit_time, most_recent_commit = commits[0]

                    except IndexError:
                        most_recent_commit = "None"
                        most_recent_commit_time = "None"

                    # checkout most recent commit here
                    if len(commits) > 0:

                        try:
                            command_checkout = (
                              'cd %s/%s%s; git checkout %s;' % (
                                self.target_repo_name,
                                self.folder_prefix,
                                team, most_recent_commit))

                            _ = self.get_command_output(command_checkout)

                        except subprocess.CalledProcessError:
                            raise subprocess.CalledProcessError

                    else:
                        print("NO VALID COMMITS FOR %s!" % team)

        except IOError:
            raise IOError("prep_repos couldn't find student records file."
                          "Run create_student_json first.")


    def get_student_team(self, student_gt_id):

        teams = self.get_dictionary_from_json_file(self.team_records_filename)

        try:
            team = teams[student_gt_id]
        except IndexError:
            raise IndexError("Couldn't find team for student with GTID %s" %
                             student_gt_id)

        return team


    def get_dictionary_from_json_file(self, file_name):

        info = {}

        if file_name not in self._dict_cache.keys():
            try:
                with open(file_name, 'r') as my_file:
                    info = json.load(my_file)
                    self._dict_cache[file_name] = info

            except IOError:
                logger.error("Couldn\'t open file with name %s", file_name)

        else:
            info = self._dict_cache[file_name]

        return info

    def get_assignment_alias(self, submission_folder_name):

        return submission_folder_name.split('/')[len(submission_folder_name.split('/')) - 1]


    def get_student_folder_names_from_list(self, whitelist, is_team_project):

        folders = []

        if is_team_project:

            teams = self.get_dictionary_from_json_file(
              self.team_members_filename)
            whitelist_teams = []

            for team in whitelist:
                group = teams[team]
                whitelist_teams += group

            whitelist = whitelist_teams # now contains student GTIDs instead of just team names

        t_square_aliases = self.get_dictionary_from_json_file(self.student_alias_filename)
        student_info = self.get_dictionary_from_json_file(self.student_records_filename)


        for student in whitelist:

            try:
                t_square_id = t_square_aliases[student]
                name = student_info[t_square_id]['name']

            except IndexError:
                logger.error("Couldn't get folder name for student with GTID %s",
                             student)

            folder_name = '%s(%s)' % (name, t_square_id)
            folders.append(folder_name)

        return folders

    def check_submission_file(self, current_student, t_square_id,
                              submission_folder_name, folder, assignment_alias):

        try:
            submission_file = '%s(%s)_submissionText.html' % (
              current_student['name'], t_square_id)

            with open(os.path.join(submission_folder_name, folder, submission_file), 'r') as submission_info:

                strings = re.findall(r'([0-9A-Za-z]{40})', submission_info.read())

                if len(strings) == 0:
                    current_student[assignment_alias]['commitID'] = "Invalid"

                else:
                    current_student[assignment_alias]['commitID'] = strings[0]    # tiebreak: use first in list
        except IOError:
            current_student[assignment_alias]['commitID'] = "Missing"

        return current_student

    def check_timestamp_file(self, current_student, submission_folder_name,
                             folder, assignment_alias):

        try:
            timestamp_file = "timestamp.txt"
            with open(os.path.join(submission_folder_name, folder, timestamp_file), 'r') as timestamp_info:
                timestamp = timestamp_info.read()
                current_student[assignment_alias]['Timestamp T-Square'] = timestamp

        except IOError:
            current_student[assignment_alias]['Timestamp T-Square'] = "Missing"
            current_student[assignment_alias]['commitID'] = "Missing"

        return current_student


    def setup_student_repo(self, current_student, is_team_project=False):

        if is_team_project:
            repo_suffix = self.get_student_team(current_student['gt_id'])

        else:
            repo_suffix = current_student['gt_id']


        if not os.path.isdir("./%s/%s%s" % (
          self.target_repo_name, self.folder_prefix, repo_suffix)):

            command = "cd %s; git clone https://github.gatech.edu/%s/%s%s.git; cd .." % (
              self.target_repo_name, self.git_context,
              self.folder_prefix, repo_suffix)
            _ = self.get_command_output(command)

            if is_team_project:
                self._pulled_teams.append(repo_suffix)  # just do this once

            just_cloned_repo = True

        else:

            just_cloned_repo = False

        # revert any local changes and pull from remote
        try:
            command_setup = "cd %s/%s%s && git clean -fd && git reset --hard HEAD && git checkout .;" % (
              self.target_repo_name, self.folder_prefix, repo_suffix)

            if self.pull_from_github and (
              not self.has_pulled_repo_for_team(
                is_team_project, repo_suffix) or
              just_cloned_repo):

                command_setup += "git pull;"

            _ = self.get_command_output(command_setup)

        except subprocess.CalledProcessError, e:

            try:
                logger.error("%s subprocess.CalledProcessError: %s",
                             current_student['gt_id'], str(e.output))

            except UnicodeDecodeError:
                logger.error("%s subprocess.CalledProcessError: "
                             "UnicodeDecodeError", current_student['gt_id'])

    def check_timestamp_github(self, current_student,
                               assignment_alias, deadline,
                               is_team_project=False):

        if not current_student[assignment_alias]['commitID valid']:
            current_student[assignment_alias]['Submission GitHub'] = 'N/A'
            current_student[assignment_alias]['Timestamp GitHub'] = 'N/A'
        else:
            if is_team_project:
                repo_suffix = self.get_student_team(current_student['gt_id'])
            else:
                repo_suffix = current_student['gt_id']

            # check timestamp of GitHub commit
            command_timestamp = (
            'cd %s/%s%s; git show -s --format=%%ci %s; cd -' % (
                self.target_repo_name, self.folder_prefix, repo_suffix,
                current_student[assignment_alias]['commitID']))
            output_timestamp = self.get_command_output(command_timestamp)

            timestamp_full = output_timestamp.split('/')[0].split(' ')
            timestamp_github_raw = (timestamp_full[0] + " " + timestamp_full[1])
            timezone_raw = timestamp_full[2].strip()
            timezone = int(int(timezone_raw) * -1) / 100

            dt_object = datetime.datetime.strptime(timestamp_github_raw, self.datetime_format)
            dt_final = dt_object + datetime.timedelta(hours=timezone)
            timestamp_github = dt_final.strftime(self.datetime_format)

            # check GitHub timestamp against deadline
            current_student[assignment_alias]['Timestamp GitHub'] = timestamp_github
            if timestamp_github < deadline:
                current_student[assignment_alias]['Submission GitHub'] = 'ok'
            else:
                current_student[assignment_alias]['Submission GitHub'] = 'late'

        return current_student

    def check_timestamp_t_square(self, current_student, assignment_alias, deadline):
        if current_student[assignment_alias]['Timestamp T-Square'] != 'Missing':
            temp = current_student[assignment_alias]['Timestamp T-Square']
            timestamp_t_square = temp[0:4] + '-' + temp[4:6] + '-' + temp[6:8] + ' ' \
                                 + temp[8:10] + ':' + temp[10:12] + ':' + temp[12:14]
            current_student[assignment_alias]['Timestamp T-Square'] = timestamp_t_square
            if timestamp_t_square <= deadline:
                current_student[assignment_alias]['Submission T-Square'] = 'ok'
            else:
                current_student[assignment_alias]['Submission T-Square'] = 'late'

        return current_student

    # Hashed?
    def check_commit_ID(self, current_student, assignment_alias, is_team_project):

        key = current_student['gt_id']
        repo_suffix = self.get_student_team(key) if is_team_project else key

        # CD First?
        command_checkout = ("cd Repos/%s%s; git checkout %s; git log --pretty=format:'%%H' -n 1; cd -" %
                             (self.folder_prefix, repo_suffix, current_student[assignment_alias]['commitID']))
        output_checkout = self.get_command_output(command_checkout)

        if platform.system() == "Windows":
            commit = output_checkout[1:len(output_checkout)-1] # windows returns \\ prefix and suffix
        else:
            commit = output_checkout.split('/')[0]

        current_student[assignment_alias]['commitID valid'] = commit == current_student[assignment_alias]['commitID']

        return current_student


    def has_pulled_repo_for_team(self, is_team_project, team_number):

        has_already_pulled = False

        if is_team_project:
            if team_number in self._pulled_teams:
                has_already_pulled = True
            else:
                self._pulled_teams.append(team_number)

        return has_already_pulled

    def get_command_output(self, command):
        my_system = platform.system()

        if my_system == 'Windows':
            command = command.replace(';', '&')    # windows chains commands with &, linux/macOS with ;
            command = command.replace('& cd -', '')    # windows doesn't support 'go back to last directory' with 'cd -', so remove it

        output = subprocess.check_output(command, shell=True)

        return output

    def generate_report(self, assignment, student_list=None,
                        report_name=None, is_team_project=False):

        try:

            student_aliases = None
            with open(self.student_alias_filename, 'r') as alias_file:
                student_aliases = json.load(alias_file)

            student_records = None
            with open(self.student_records_filename, 'r') as records_file:
                student_records = json.load(records_file)

            bad_commit, late_github, late_t_square, missing = [], [], [], []

            init_log(log_name=report_name)
            logger.info("Report: %s\n", assignment)

            if is_team_project:

                teams = self.get_dictionary_from_json_file(
                  self.team_members_filename)

                new_student_list = []

                for team in student_list:
                    members_list = teams[team]

                    new_student_list.append(team)
                    new_student_list.extend(members_list)

                student_list = new_student_list

            elif student_list is None or not student_list:
                student_list = student_aliases.keys() # all student_list!
            else:
                pass # Do nothing


            for student in student_list:

                if not student: # ignore whitespace/blank lines
                    continue

                if is_team_project and "Team" in student:
                    logger.info("\n========== %s ==========", student)
                    continue

                student_info = student_records[student_aliases[student]]

                logger.info(student)

                if assignment not in student_info:

                    logger.info('\tNo records found')
                    missing.append(student)
                    continue

                student_info_assignment = student_info[assignment]
                for key in sorted(student_info_assignment.keys(), reverse=True):

                    student_info_assignment_key = student_info_assignment[key]
                    logger.info('\t%s: %s', key, student_info_assignment_key)

                    if (key == 'Submission GitHub' and
                          student_info_assignment_key == 'late'):
                        late_github.append(student)

                    if (key == 'Submission T-Square' and
                          student_info_assignment_key == 'late'):
                        late_t_square.append(student)

                    if (key == 'commitID' and
                          student_info_assignment_key == 'Missing'):
                        missing.append(student)

                    if (key == 'commitID valid' and
                          student_info_assignment_key == False):
                        bad_commit.append(student)

            logger.info("\n========== RESULTS ==========")
            str_buffer = []
            str_buffer.append("\nLATE SUBMISSIONS:")
            for fmt_str, data in [("\tT-Square (%d): %s", late_t_square),
                                  ("\tGitHub (%d): %s", late_github),
                                  ("\nMISSING SUBMISSIONS (%s): %s", missing),
                                  ("\nBAD COMMITS (%s):\n\t%s", bad_commit)]:
                str_buffer.append(fmt_str % (len(data), ", ".join(data)))
            logger.info("\n".join(str_buffer))

        except IOError:
            msg = (("generate_report couldn't find %s file."
                    "Try running create_student_json first.") %
                   self.student_alias_filename)
            print(msg)
            raise IOError(msg)


    def commit_id_present(self, commitID_message):
        return commitID_message != 'Invalid' and commitID_message != 'Missing'


def init_log(log_name=None, log_file_mode='w', fmt_str=None):
    """
    Initialize Logging

    This should not be in a class as this is unique per file (program file).

    This could be integrated by moving all logging commands but then all
    log names need to be unique to prevent clobbering. The default action is
    to append but set to overwrite since it is unlikely we need previous run
    info.
    """


    if log_name == "":
        log_name = 'submission_runner.txt'

    if fmt_str is None or not fmt_str:
        fmt_str = "%(message)s"
        # Enable for more timing info
        #fmt_str="%(asctime)s - %(name)30s - %(levelname)10s: %(message)s"

    fmt_str = logging.Formatter(fmt_str)
    logger.setLevel(logging.DEBUG)

    stdout = logging.StreamHandler()
    stdout.setFormatter(fmt_str)
    stdout.setLevel(logging.INFO)
    logger.addHandler(stdout)

    if log_name is not None:
        fout = logging.FileHandler(filename=log_name, mode=log_file_mode)
        fout.setFormatter(fmt_str)
        fout.setLevel(logging.DEBUG)
        logger.addHandler(fout)

