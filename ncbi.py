#!/usr/bin/env python
'''This script contains a number of utilities for submitting our analyses
to NCBI's Genbank and SRA databases.
'''

__author__ = "PLACEHOLDER"
__commands__ = []

import argparse, logging, collections, shutil, os, os.path, glob
import Bio.SeqIO
import util.cmd, util.file, util.version
import tools.tbl2asn
import interhost

log = logging.getLogger(__name__)



def fasta_chrlens(fasta):
    out = collections.OrderedDict()
    with open(fasta, 'rt') as inf:
        for seq in Bio.SeqIO.parse(inf, 'fasta'):
            out[seq.id] = len(seq)
    return out

def tbl_transfer(ref_fasta, ref_tbl, alt_fasta, out_tbl, oob_clip=False):
    ''' This function takes an NCBI TBL file describing features on a genome
        (genes, etc) and transfers them to a new genome.
    '''
    cmap = interhost.CoordMapper(ref_fasta, alt_fasta)
    alt_chrlens = fasta_chrlens(alt_fasta)
    
    with open(ref_tbl, 'rt') as inf:
        with open(out_tbl, 'wt') as outf:
            for line in inf:
                line = line.rstrip('\r\n')
                if not line:
                    pass
                elif line.startswith('>'):
                    # sequence identifier
                    if not line.startswith('>Feature '):
                        raise Exception("not sure how to handle a non-Feature record")
                    seqid = line[len('>Feature '):].strip()
                    if not (seqid.startswith('gb|') and seqid.endswith('|') and len(seqid)>4):
                        raise Exception("reference annotation does not refer to a GenBank accession")
                    seqid = seqid[3:-1]
                    altid = cmap.mapAtoB(seqid)
                    line = '>Feature ' + altid
                    feature_keep = True
                elif line[0] != '\t':
                    # feature with numeric coordinates (map them)
                    row = line.split('\t')
                    if not len(row)>=2:
                        raise Exception("this line has only one column?")
                    row[0] = int(row[0])
                    row[1] = int(row[1])
                    if row[1] >= row[0]:
                        row[0] = cmap.mapAtoB(seqid, row[0], -1)[1]
                        row[1] = cmap.mapAtoB(seqid, row[1],  1)[1]
                    else:
                        # negative strand feature
                        row[0] = cmap.mapAtoB(seqid, row[0],  1)[1]
                        row[1] = cmap.mapAtoB(seqid, row[1], -1)[1]
                    
                    if row[0] and row[1]:
                        feature_keep = True
                    elif row[0]==None and row[1]==None:
                        # feature completely out of bounds
                        feature_keep = False
                        continue
                    else:
                        # feature overhangs end of sequence
                        if oob_clip:
                            if row[0]==None:
                                row[0] = '<1'
                            if row[1]==None:
                                row[1] = '>{}'.format(alt_chrlens[altid])
                        else:
                            feature_keep = False
                            continue
                    line = '\t'.join(map(str,row))
                else:
                    # feature notes
                    if not feature_keep:
                        # skip any lines that follow a skipped feature
                        continue
                    elif 'protein_id' in line:
                        # skip any lines that refer to an explicit protein_id
                        continue
                outf.write(line+'\n')

def parser_tbl_transfer(parser=argparse.ArgumentParser()):
    parser.add_argument("ref_fasta", help="Input sequence of reference genome")
    parser.add_argument("ref_tbl", help="Input reference annotations (NCBI TBL format)")
    parser.add_argument("alt_fasta", help="Input sequence of new genome")
    parser.add_argument("out_tbl", help="Output file with transferred annotations")
    parser.add_argument('--oob_clip', default=False, action='store_true',
        help='''Out of bounds feature behavior.
        False: drop all features that are completely or partly out of bounds
        True:  drop all features completely out of bounds
               but truncate any features that are partly out of bounds''')
    util.cmd.common_args(parser, (('tmpDir',None), ('loglevel',None), ('version',None)))
    util.cmd.attach_main(parser, tbl_transfer, split_args=True)
    return parser
__commands__.append(('tbl_transfer', parser_tbl_transfer))


def fasta2fsa(infname, outdir):
    ''' copy a fasta file to a new directory and change its filename to end in .fsa
        for NCBI's sake. '''
    outfname = os.path.basename(infname)
    if outfname.endswith('.fasta'):
        outfname = outfname[:-6]
    elif outfname.endswith('.fa'):
        outfname = outfname[:-3]
    if not outfname.endswith('.fsa'):
        outfname = outfname + '.fsa'
    outfname = os.path.join(outdir, outfname)
    shutil.copyfile(infname, outfname)
    return outfname

def make_structured_comment_file(seq_tech):
    cmt_fname = util.file.mkstempfname('.cmt')
    with open(cmt_fname, 'wt') as outf:
        outf.write("StructuredCommentPrefix\t##Genome-Assembly-Data-START##\n")
        outf.write("Assembly Method\tgithub.com/broadinstitute/viral-ngs v. {}\n".format(
            util.version.get_version()))
        if seq_tech:
            outf.write("Sequencing Technology\t{}\n".format(seq_tech))
    return cmt_fname

def prep_genbank_files(templateFile, fastaDir, annotDir,
        master_source_table=None, comment=None, sequencing_tech=None, source_modifiers=[]):
    ''' Prepare genbank submission files.  Requires .fasta and .tbl files as input,
        as well as numerous other metadata files for the submission.  Creates a
        directory full of files (.sqn in particular) that can be sent to GenBank.
    '''
    # make output directory
    util.file.mkdir_p(annotDir)
    for fn in glob.glob(os.path.join(fastaDir, '*.fasta')):
        fasta2fsa(fn, annotDir)
        if master_source_table:
            srcfile = os.path.join(annotDir, os.path.basename(fn)[:-6] + '.src')
            shutil.copy(master_source_table, srcfile)
    
    # source modifiers
    if source_modifiers:
        source_modifiers = list(x.split('=') for x in source_modifiers)
    
    # create assembly data structured comment file
    cmt_fname = make_structured_comment_file(sequencing_tech)
    
    # run tbl2asn
    tbl2asn = tools.tbl2asn.Tbl2AsnTool()
    tbl2asn.execute(templateFile, annotDir,
        comment=comment, structured_comment_file=cmt_fname,
        source_quals=source_modifiers)
    
    os.unlink(cmt_fname)

def parser_prep_genbank_files(parser=argparse.ArgumentParser()):
    parser.add_argument('templateFile',
        help='Submission template file (.sbt) including author and contact info')
    parser.add_argument("fastaDir",
        help="Input directory with fasta files")
    parser.add_argument("annotDir",
        help="Output directory with genbank submission files (.tbl files must already be there)")
    parser.add_argument('--comment', default=None,
        help='comment field')
    parser.add_argument('--sequencing_tech', default=None,
        help='sequencing technology (e.g. Illumina HiSeq 2500)')
    parser.add_argument('--master_source_table', default=None,
        help='source modifier table')
    parser.add_argument('--source_modifiers', default=[], nargs='*',
        help='source modifiers')
    util.cmd.common_args(parser, (('tmpDir',None), ('loglevel',None), ('version',None)))
    util.cmd.attach_main(parser, prep_genbank_files, split_args=True)
    return parser
__commands__.append(('prep_genbank_files', parser_prep_genbank_files))


def prep_sra_table(lib_fname, biosampleFile, md5_fname, outFile):
    ''' This is a very lazy hack that creates a basic table that can be
        pasted into various columns of an SRA submission spreadsheet.  It probably
        doesn't work in all cases.
    '''
    metadata = {}

    with open(biosampleFile, 'rU') as inf:
        header = inf.readline()
        for line in inf:
            row = line.rstrip('\n\r').split('\t')
            metadata.setdefault(row[0], {})
            metadata[row[0]]['biosample_accession'] = row[1]

    with open(md5_fname, 'rU') as inf:
        for line in inf:
            row = line.rstrip('\n\r').split()
            s = os.path.basename(row[1])
            assert s.endswith('.cleaned.bam')
            s = s[:-len('.cleaned.bam')]
            metadata.setdefault(s, {})
            metadata[s]['filename'] = row[1]
            metadata[s]['MD5_checksum'] = row[0]
    
    with open(outFile, 'wt') as outf:
        header = ['biosample_accession', 'sample_name', 'library_ID', 'filename', 'MD5_checksum']
        outf.write('\t'.join(header)+'\n')
        with open(lib_fname, 'rU') as inf:
            for line in inf:
                lib = line.rstrip('\n\r')
                parts = lib.split('.')
                assert len(parts)>1 and parts[-1].startswith('l')
                s = '.'.join(parts[:-1])
                metadata.setdefault(s, {})
                metadata[s]['library_ID'] = lib
                metadata[s]['sample_name'] = s
                outf.write('\t'.join(metadata[s].get(h,'') for h in header)+'\n')

def parser_prep_sra_table(parser=argparse.ArgumentParser()):
    parser.add_argument('lib_fname',
        help='A file that lists all of the library IDs that will be submitted in this batch')
    parser.add_argument("biosampleFile",
        help="""A file with two columns and a header: sample and BioSample.
        This file may refer to samples that are not included in this submission.""")
    parser.add_argument("md5_fname",
        help="""A file with two columns and no header.  Two columns are MD5 checksum and filename.
        Should contain an entry for every bam file being submitted in this batch.
        This is typical output from "md5sum *.cleaned.bam".""")
    parser.add_argument("outFile",
        help="Output table that contains most of the variable columns needed for SRA submission.")
    util.cmd.common_args(parser, (('loglevel',None), ('version',None)))
    util.cmd.attach_main(parser, prep_sra_table, split_args=True)
    return parser
__commands__.append(('prep_sra_table', parser_prep_sra_table))


def full_parser():
    return util.cmd.make_parser(__commands__, __doc__)
if __name__ == '__main__':
    util.cmd.main_argparse(__commands__, __doc__)
