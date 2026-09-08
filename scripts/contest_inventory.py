"""Reviewed contest order, I/O names and limits from the supplied papers."""
# contest, year, group, day, PDF filename, ordered (basename, Luogu ID) pairs
SESSIONS = [
 ('NOIP',2014,'junior',1,'noip2014fj.pdf','count:P2141 ratio:P2118 matrix:P2239 submatrix:P2258'),
 ('NOIP',2014,'senior',1,'NOIP2014-day1Senior.pdf','rps:P1328 link:P1351 bird:P1941'),
 ('NOIP',2014,'senior',2,'noip2014fs2.pdf','wireless:P2038 road:P2296 equation:P2312'),
 ('NOIP',2015,'junior',1,'noip2015fj.pdf','coin:P2669 mine:P2670 sum:P2671 salesman:P2672'),
 ('NOIP',2015,'senior',1,'noip2015fs1.pdf','magic:P2615 message:P2661 landlords:P2668'),
 ('NOIP',2015,'senior',2,'noip2015fs2.pdf','stone:P2678 substring:P2679 transport:P2680'),
 ('NOIP',2016,'junior',1,'noip2016fj.pdf','pencil:P1909 date:P2010 port:P2058 magic:P2119'),
 ('NOIP',2016,'senior',1,'noip2016fs1.pdf','toy:P1563 running:P1600 classroom:P1850'),
 ('NOIP',2016,'senior',2,'noip2016fs2.pdf','problem:P2822 earthworm:P2827 angrybirds:P2831'),
 ('NOIP',2018,'senior',1,'NOIP2018FinalSeniorDay1TestPaper.pdf','road:P5019 money:P5020 track:P5021'),
 ('NOIP',2018,'senior',2,'NOIP2018FinalSeniorDay2TestPaper.pdf','travel:P5022 game:P5023 defense:P5024'),
 ('NOIP',2020,'senior',1,'noip2020.pdf','water:P7113 string:P7114 ball:P7115 walk:P7116'),
 ('NOIP',2021,'senior',1,'noip2021.pdf','number:P7960 sequence:P7961 variance:P7962 chess:P7963'),
 ('NOIP',2022,'senior',1,'2022 CCF NOIP 正式赛试题.pdf','plant:P8865 meow:P8866 barrack:P8867 match:P8868'),
 ('NOIP',2025,'senior',1,'noip_2025.pdf','candy:P14635 sale:P14636 tree:P14637 query:P14638'),
 ('CSP-S',2019,'senior',1,'2019-CCF-CSP-S2-day1.pdf','code:P5657 brackets:P5658 tree:P5659'),
 ('CSP-S',2019,'senior',2,'2019-CCF-CSP-S2-day2.pdf','meal:P5664 partition:P5665 centroid:P5666'),
 ('CSP-S',2020,'senior',1,'2020_CSP-S2.pdf','julian:P7075 zoo:P7076 call:P7077 snakes:P7078'),
 ('CSP-S',2021,'senior',1,'2021 CSP-S2.pdf','airport:P7913 bracket:P7914 palin:P7915 traffic:P7916'),
 ('CSP-S',2024,'senior',1,'CSP-S.pdf','duel:P11231 detect:P11232 color:P11233 arena:P11234'),
 ('CSP-S',2025,'senior',1,'day1.pdf','club:P14361 road:P14362 replace:P14363 employ:P14364'),
]

# Seconds / MiB; all unlisted entries are 1 second and the year's default memory.
LIMITS = {
 'P2668':(2,1024), 'P2680':(1,256), 'P5659':(2,256), 'P5665':(2,1024), 'P5666':(3,256),
 'P1600':(2,512), 'P2831':(2,512), 'P5021':(1,512),
 'P7963':(4,1024), 'P8868':(2,512), 'P14637':(2,512), 'P14638':(2,512),
 'P7077':(2,256), 'P7078':(2,256), 'P7916':(3,512), 'P11232':(2,512), 'P14363':(1,2048),
}
# Non-fulltext outputs must not be sent to the legacy comparator.
SPECIAL = {'P1850':'浮点误差比较器待接入', 'P7115':'构造题需专用校验器', 'P8866':'构造题需专用校验器'}
