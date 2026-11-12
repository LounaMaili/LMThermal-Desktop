"""Small noncanonical synthetic plans; the 51 pinned corpus files are never rewritten."""
from copy import deepcopy
from pathlib import Path
from lmtr_reference import Reader
from lmtr_reference.jsoncodec import dumps
from lmtr_reference.metadata import sha

FILES=Path(__file__).parent/'fixtures/lmtr/files'


def base_plan():
    """Reuse verified metadata and explicit original tiny Float32/mask recipes."""
    with Reader(FILES/'illustration-01-stored.lmtr') as reader:
        head=deepcopy(reader.head)
        meta=deepcopy(reader.metadata(reader.record(reader.header_end)))
    return {'header':head,'chunks':[{'metadata':meta,'payload_recipes':{
        '1:temperature':{'kind':'hex','hex':'0000a0410000f04100000080000000000000c0410000d041'},
        '1:validity':{'kind':'hex','hex':'010101000101'}}}]}


def rehash_segment(meta):
    """Content IDs bind the exact synthetic semantic definitions, as the wire requires."""
    for segment in meta['segments']:
        segment['id']=sha(dumps({k:v for k,v in segment.items() if k!='id'}))
    for entry in meta['entries']: entry['segment_id']=meta['segments'][0]['id']


def grid_plan(width=7,height=4,codec=0):
    """Constant Float32 primary plane with no mask; no physical temperature meaning."""
    plan=base_plan();plan['header']['codecs']=[codec]
    item=plan['chunks'][0];meta=item['metadata'];stream=meta['segments'][0]['streams'][0]
    geometry=stream['geometry'];geometry.update(width_px=width,height_px=height)
    observation=meta['entries'][1];payload=observation['payloads'][0]
    payload.update(geometry=deepcopy(geometry),shape=[height,width],length=str(width*height*4))
    observation['payloads']=[payload]
    observation['measurement'].update(validity='all_valid')
    observation['measurement'].pop('mask_payload_id',None);observation['measurement'].pop('statistics',None)
    meta['blocks']=[meta['blocks'][0]];meta['blocks'][0].update(codec=codec,decoded=str(width*height*4),stored=str(width*height*4))
    item['payload_recipes']={'1:temperature':{'kind':'f32_constant','value':25,'count':width*height}}
    rehash_segment(meta)
    return plan


def segmented_plan(*,same_geometry=False):
    """Two explicit source sessions; second grid can still contain the old ROI bounds."""
    plan=base_plan();second=grid_plan(3 if same_geometry else 7,2 if same_geometry else 4)['chunks'][0]
    first=plan['chunks'][0]['metadata'];first['entries'][-1]['reason']='source_configuration_changed'
    meta=second['metadata'];segment=meta['segments'][0]
    segment.update(reason='source_configuration_changed',start_entry='3',start_timeline_ns='3000000')
    stream=segment['streams'][0];stream['session_id']='00000000-0000-4000-8000-000000000066'
    for i,entry in enumerate(meta['entries']):
        entry['entry_id']=str(i+3);entry['timeline_ns']=str((i+3)*1000000)
    second['payload_recipes']={'4:temperature':second['payload_recipes']['1:temperature']}
    rehash_segment(meta)
    plan['chunks'].append(second)
    return plan


def gap_plan():
    """One truthful simulated known loss followed by segment closure, no fake matrix."""
    plan=base_plan();meta=plan['chunks'][0]['metadata']
    end=meta['entries'].pop();end.update(entry_id='3',timeline_ns='3000000')
    meta['entries'].append({'kind':'gap','entry_id':'2','timeline_ns':'2000000','segment_id':meta['segments'][0]['id'],
        'stream_id':'primary','category':'source_loss','reason':'synthetic_missing_observation','count':'1',
        'observation_first':'1','observation_last':'1','device_first':None,'device_last':None,'scheduled':None,
        'evidence':{'basis':'host_observation','before':None,'after':None}})
    meta['entries'].append(end)
    return plan


def opaque_plan():
    """An opaque observation legitimately has no primary geometry or Celsius plane."""
    plan=base_plan();plan['header']['streams'][0]['purpose']='opaque'
    item=plan['chunks'][0];meta=item['metadata'];stream=meta['segments'][0]['streams'][0]
    stream['geometry']=None
    stream['capabilities']={k:('supported' if k=='acquisition' else 'unsupported') for k in stream['capabilities']}
    stream['encodings']=[{'role':'acquisition','encoding_id':'org.example.opaque-v1'}]
    stream['policy'].update(level='full',requested_roles=['acquisition'])
    entry=meta['entries'][1]
    entry['measurement']={'status':'unsupported','reason':'no_temperature_capability'}
    for k in entry['retention']:
        entry['retention'][k]={'status':'present'} if k=='acquisition' else {'status':'unsupported','reason':'unsupported'}
    p={'id':'acquisition','role':'acquisition','kind':'materialized','block_id':'b0','offset':'0','length':'16',
       'hash':'0'*64,'encoding_id':'org.example.opaque-v1','geometry':None,'dtype':'bytes','byte_order':'not_applicable',
       'shape':[16],'unit':None,'layout':'opaque','lossiness':'lossless'}
    entry['payloads']=[p]
    meta['blocks']=[{'id':'b0','role':'acquisition','encoding_id':p['encoding_id'],'dtype':'bytes','byte_order':'not_applicable',
                     'offset':'0','codec':0,'decoded':'16','stored':'16','hash':'0'*64}]
    item['payload_recipes']={'1:acquisition':{'kind':'hex','hex':'00112233445566778899aabbccddeeff'}}
    rehash_segment(meta)
    return plan
