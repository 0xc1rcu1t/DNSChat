#!/usr/bin/env python3
#
# DNSChat - Proof of Concept Implementation (DNSCHAT-2)
#
# http://projects.bentasker.co.uk/jira_projects/browse/DNSCHAT.html
#
# Copyright (C) 2015 B Tasker
# Released under GNU GPL V2
# See http://www.gnu.org/licenses/gpl-2.0.html
#
#
# Dependancies (Ubuntu Package names)
# 	python-scapy
# python-gnupg
#

from scapy.all import *
import threading
import os
import sys
import time
import gnupg
import json
import dns.resolver
import random
import re
import getpass
import getopt
import binascii

# Scapy likes to complain if there isn't an IPv6 route, so lets shut it up
import logging
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)


listener = None
listenerthread = None
cryptobj = None
debug = False


class ChatListen(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        global listenerthread
        self.current_value = None
        self.running = True
        self.key = None
        self.debug = False
        self.buffer = {}

    def run(self):
        global listener
        while self.running:
            self.listen()

    def listen(self):
        sniff(filter="port 53", prn=self.process_pkt, timeout=10)

    def process_pkt(self, pkt):
        if DNSQR in pkt and pkt.dport == 53:
            # Break the query down into its constituent parts
            eles = pkt[DNSQR].qname.decode().split('.')
            seqid = eles[1]
            seqno = eles[2]

            # This is a somewhat restrictive requirement and could easily be improved, but it works well enough for a PoC
            match = re.search("^\d+$", eles[1])
            try:
                x = match.group(0)
            except AttributeError:
                return

            # Ignore messages that we've sent
            if int(eles[0]) == self.myid:
                return

            if debug:
                print('Received part ' + str(eles[2]) + '/' + str(
                    eles[3]) + ' for msg sequence ' + seqid + ' from user ' + str(eles[0]))

            # Create an entry in the dict if there isn't one already
            if 'seq'+seqid not in self.buffer:
                self.buffer['seq'+seqid] = {}
                self.buffer['seq'+seqid]['user'] = eles[0]
                self.buffer['seq'+seqid]['entries'] = {}
                self.buffer['seq'+seqid]['seqlen'] = eles[3]
                self.buffer['seq'+seqid]['output'] = False

            # Set the details for this entry
            self.buffer['seq'+seqid]['entries'][seqno] = eles[4]

            # Once the full dispatch has been received, re-assemble and output.
            if len(self.buffer['seq'+seqid]['entries']) == int(self.buffer['seq'+seqid]['seqlen']) and not self.buffer['seq'+seqid]['output']:
                compiled = ''
                # Re-assemble the messages in order
                for key, value in sorted(self.buffer['seq'+seqid]['entries'].items(), key=lambda key_value: int(key_value[0])):
                    compiled += value

                clear = self.cryptobj.decrypt(binascii.unhexlify(compiled))

                try:
                    obj = json.loads(clear)
                except:
                    # If we couldn't decrypt it, the key being used is probably wrong
                    print(
                        '[Warning]: Received a message that could not be decrypted')
                    # Prevent repetition of the warning
                    self.buffer['seq'+seqid]['output'] = True
                    return

                ts = time.strftime('%H:%M:%S', time.localtime(obj['t']))

                # Output the message (yes, this should be somewhere else really)
                ts = time.strftime('%H:%M:%S', time.localtime(obj['t']))

                # Output the message (yes, this should be somewhere else really)
                print(ts + ' [' + str(obj['f']) + '] ' + obj['m'])

                # Mark the message as output
                self.buffer['seq'+seqid]['output'] = True


class DNSChatCrypto:
    def __init__(self, key, passphrase):
        self.gpg = gnupg.GPG()
        import_results = self.gpg.import_keys(key)

        if import_results.count == 0:
            raise ValueError("Failed to import key")

        self.keystring = import_results.key_fingerprints[0]
        self.passphrase = passphrase
        self.cryptkey = hashlib.sha256(self.keystring.encode()).digest()[:32]
        self.cryptobj = AES.new(self.cryptkey, AES.MODE_CFB, IV=b'0' * 16)

    def encrypt(self, msg):
        encrypted = self.gpg.encrypt(
            msg, self.keystring, symmetric='AES256', passphrase=self.passphrase)
        return binascii.hexlify(encrypted.data).decode()

    def decrypt(self, ciphertext):
        decrypted = self.cryptobj.decrypt(
            binascii.unhexlify(ciphertext)).decode()
        return decrypted


def main(argv):
    ''' Starting point.....
    '''
    myid = False
    resolve = False
    domain = False
    charlimit = 63
    passphrase = ""

    # Process the command-line arguments
    try:
        opts, args = getopt.getopt(argv, "vhr:i:d:c:", [
                                   "debug", "help", "resolver=", "id=", "domain-suffix=", "char-limit="])
    except getopt.GetoptError:
        usage()
        sys.exit(2)

    for opt, arg in opts:
        if opt in ("-v", "--debug"):
            global debug
            debug = True
        elif opt in ("-h", "--help"):
            usage()
            sys.exit(2)
        elif opt in ("-r", "--resolver"):
            resolve = dns.resolver.Resolver(configure=False)
            resolve.nameservers = [arg]
        elif opt in ("-i", "--id"):
            if int(arg) > 0:
                myid = arg
        elif opt in ("-d", "--domain-suffix"):
            domain = arg
        elif opt in ("-c", "--char-limit"):
            charlimit = int(arg)

    if not myid:
        myid = random.randint(1, 99)

    if not resolve:
        resolve = dns.resolver.Resolver()

    if not domain:
        domain = input('Enter the domain to query: ')

    # Get symmetric passphrase
    passphrase = getpass.getpass(
        "Enter Symmetric passphrase to use for this session: ")

    # Get things rolling
    launch(resolve, myid, domain, charlimit, passphrase)


def usage():
    ''' Output the usage information

    '''
    print('')
    print('-h/--help		Print this text')
    print('-r/--resolver=		DNS Resolver to use (e.g. --resolver=8.8.8.8)')
    print('-c/--char-limit=		The maximum number of characters to use per query (default 63 - max is also 63)')
    print('-i/--id=		Numeric ID to use')
    print('-d/--domain=		The domain to query (e.g. --domain=example.com)')
    print('-v/--debug		Use debug mode')
    return


def launch(resolve, myid, domain, charlimit, passphrase):
    ''' Launch the threads

            This used to be main() and then I dropped in support for command line arguments
    '''

    global cryptobj
    global listenerthread
    global debug

    if debug:
        print('Running with the following values')
        print('	Resolver:' + str(resolve.nameservers))
        print('	My ID:' + str(myid))
        print('	Domain:' + str(domain))
        print('')

    # Get the passphrase to use
    key = getpass.getpass(
        'Enter Symmetric passphrase to use for this session: ')

    cryptobj = DNSChatCrypto(key, passphrase)
    myid = myid

    listenerthread = ChatListen()
    listenerthread.cryptobj = cryptobj
    listenerthread.myid = myid
    listenerthread.debug = debug
    listenerthread.start()
    seqid = random.randint(0, 999)

    try:
        while True:
            msgstring = {}
            msg = input('Enter a Message: ')
            epoch_time = int(time.time())
            msgstring['t'] = epoch_time
            msgstring['m'] = msg

            # Encrypt the message
            # Example:  {"msg": "A test", "time": 1421148145}
            ciphertext = cryptobj.encrypt(json.dumps(msgstring))

            testlen = len(str(myid)+'.'+str(seqid) +
                          '.99.1000..'+domain) + charlimit

            while testlen >= 253 or charlimit > 63:
                # We're likely to hit a limit on DNS name length (63 bytes per label, 253 bytes for the entire domain name)
                charlimit -= 5
                testlen = len(str(myid)+'.'+str(seqid) +
                              '.99.1000..'+domain) + charlimit
                if debug:
                    print('Charlimit lowered to ' + str(charlimit))
                if charlimit < 15:
                    print(
                        '[System]: Available character length is getting low. Consider exiting and re-connecting')

            # Break the message down into suitable chunks
            charlimit  # No more than 40 chars per request
            chunks = [ciphertext[i:i+charlimit]
                      for i in range(0, len(ciphertext), charlimit)]

            # Calculate the number of requests that will be made
            TN = str(len(chunks))

            for seqno, msg in enumerate(chunks):
                try:
                    if debug:
                        print('Querying: ') + str(myid)+'.'+str(seqid) + \
                            '.'+str(seqno)+'.'+TN+'.'+str(msg)+'.'+domain
                    # Send the query
                    resp = resolve.query(str(
                        myid)+'.'+str(seqid)+'.'+str(seqno)+'.'+TN+'.'+str(msg)+'.'+domain, 'A').response
                except:
                    # Don't raise an exception on NXDOMAIN
                    continue

            # Output a copy of the text
            ts = time.strftime('%H:%M:%S', time.localtime(msgstring['t']))
            print(ts + ' [You]: ' + msgstring['m'])

            # increment the sequence number
            seqid += 1

    except (KeyboardInterrupt, SystemExit):
        listenerthread.running = False
        print('')
        print('Exiting....')
        listenerthread.join()  # Let the thread finish


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (KeyboardInterrupt, SystemExit):
        print('Exiting')
        sys.exit()
